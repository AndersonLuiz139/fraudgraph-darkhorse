"""
Gera uma base FICTÍCIA de dados financeiros com padrões de fraude "plantados".

Saída: pasta dados/ com arquivos CSV + gabarito.json (o que foi plantado).

Uso:
    python gerar_dados.py
"""
import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from faker import Faker

SEED = 42
random.seed(SEED)
fake = Faker("pt_BR")
Faker.seed(SEED)

PASTA = Path(__file__).parent / "dados"
PASTA.mkdir(exist_ok=True)

INICIO = datetime(2026, 9, 1)
FIM = datetime(2026, 10, 31)
BANCOS = ["Banco Alfa", "Banco Beta", "Banco Gama", "Banco Delta", "Banco Ômega"]

clientes, contas, enderecos, telefones, dispositivos = [], [], [], [], []
cliente_dispositivo, transacoes = [], []
cont = {"cli": 0, "ct": 0, "end": 0, "tel": 0, "disp": 0, "tx": 0}


def novo_id(tipo, prefixo):
    cont[tipo] += 1
    return f"{prefixo}-{cont[tipo]:04d}"


def novo_endereco(cidade=None):
    e = {"id": novo_id("end", "END"), "logradouro": fake.street_address(),
         "cidade": cidade or fake.city(), "uf": fake.estado_sigla()}
    enderecos.append(e)
    return e["id"]


def novo_telefone():
    t = {"id": novo_id("tel", "TEL"), "numero": fake.cellphone_number()}
    telefones.append(t)
    return t["id"]


def novo_dispositivo(tipo=None):
    d = {"id": novo_id("disp", "DISP"), "tipo": tipo or random.choice(["Android", "iPhone", "Notebook"]),
         "ip": fake.ipv4_public()}
    dispositivos.append(d)
    return d["id"]


def novo_cliente(nome, tipo, renda, endereco_id, telefone_id, data_cadastro, perfil):
    c = {"id": novo_id("cli", "CLI"), "nome": nome, "tipo": tipo,
         "documento": fake.cpf() if tipo == "PF" else fake.cnpj(),
         "idade": random.randint(19, 78) if tipo == "PF" else "",
         "renda_mensal": renda, "endereco_id": endereco_id, "telefone_id": telefone_id,
         "data_cadastro": data_cadastro.date().isoformat(), "perfil_real": perfil}
    clientes.append(c)
    return c


def nova_conta(cliente, data_abertura):
    c = {"id": novo_id("ct", "CT"), "cliente_id": cliente["id"], "banco": random.choice(BANCOS),
         "agencia": f"{random.randint(1, 9999):04d}", "tipo": "PJ" if cliente["tipo"] == "PJ" else "Corrente",
         "data_abertura": data_abertura.date().isoformat()}
    contas.append(c)
    return c["id"]


def tx(origem, destino, valor, quando, canal="PIX"):
    transacoes.append({"id": novo_id("tx", "TX"), "origem": origem, "destino": destino,
                       "valor": round(valor, 2), "data_hora": quando.strftime("%Y-%m-%dT%H:%M:%S"),
                       "canal": canal})


def momento_aleatorio(ini=INICIO, fim=FIM):
    seg = int((fim - ini).total_seconds())
    return ini + timedelta(seconds=random.randint(0, seg))


# ---------------------------------------------------------------------------
# 1) CLIENTES LEGÍTIMOS
# ---------------------------------------------------------------------------
pf_contas = []
casal_pendente = None
for i in range(150):
    # ~20% moram com outra pessoa (casal/família) -> endereço compartilhado por 2 (normal!)
    if casal_pendente and random.random() < 0.5:
        end_id = casal_pendente
        casal_pendente = None
    else:
        end_id = novo_endereco()
        casal_pendente = end_id if random.random() < 0.25 else None
    cadastro = fake.date_time_between(datetime(2015, 1, 1), datetime(2025, 6, 1))
    c = novo_cliente(fake.name(), "PF", round(random.uniform(1800, 18000), 2), end_id,
                     novo_telefone(), cadastro, "legitimo")
    pf_contas.append(nova_conta(c, cadastro))
    cliente_dispositivo.append({"cliente_id": c["id"], "dispositivo_id": novo_dispositivo()})
    if random.random() < 0.3:  # alguns têm 2 aparelhos
        cliente_dispositivo.append({"cliente_id": c["id"], "dispositivo_id": novo_dispositivo("Notebook")})

pj_contas = []
for i in range(10):
    cadastro = fake.date_time_between(datetime(2012, 1, 1), datetime(2024, 1, 1))
    c = novo_cliente(fake.company(), "PJ", round(random.uniform(40000, 250000), 2), novo_endereco(),
                     novo_telefone(), cadastro, "legitimo")
    pj_contas.append(nova_conta(c, cadastro))
    cliente_dispositivo.append({"cliente_id": c["id"], "dispositivo_id": novo_dispositivo("Notebook")})

# ---------------------------------------------------------------------------
# 2) TRANSAÇÕES LEGÍTIMAS
# ---------------------------------------------------------------------------
for _ in range(450):  # Pix entre pessoas
    o, d = random.sample(pf_contas, 2)
    tx(o, d, min(random.lognormvariate(5.3, 0.9), 6000), momento_aleatorio())
for _ in range(900):  # compras no comércio (lojas recebem de MUITAS pessoas -> não é fraude)
    tx(random.choice(pf_contas), random.choice(pj_contas), min(random.lognormvariate(4.6, 0.8), 3000),
       momento_aleatorio(), random.choice(["PIX", "CARTAO"]))
for pj in pj_contas:  # salários: lojas pagam funcionários
    for func in random.sample(pf_contas, 4):
        for mes in (9, 10):
            tx(pj, func, random.uniform(1600, 3500), datetime(2026, mes, 5, 9, random.randint(0, 59)), "TED")
for _ in range(25):  # fornecedores entre empresas
    o, d = random.sample(pj_contas, 2)
    tx(o, d, random.uniform(1500, 8000), momento_aleatorio(), "TED")
for _ in range(4):  # valores ALTOS mas legítimos (ex.: compra de carro) -> valor alto sozinho não é fraude
    o, d = random.sample(pf_contas, 2)
    tx(o, d, random.uniform(35000, 80000), momento_aleatorio(), "TED")

# ---------------------------------------------------------------------------
# 3) FRAUDE A - "QUADRILHA" com 6 identidades sintéticas
#    Compartilham dispositivo, telefone e endereço; contas abertas recentemente.
# ---------------------------------------------------------------------------
disp_q = novo_dispositivo("Android")
tel_q = novo_telefone()
end_q = novo_endereco("São Paulo")
disp_q2 = novo_dispositivo("Notebook")
quadrilha = []
for i in range(6):
    tel = tel_q if i < 3 else novo_telefone()          # 3 com o MESMO telefone
    end = end_q if i < 4 else novo_endereco()           # 4 no MESMO endereço
    cadastro = datetime(2026, 8, 10) + timedelta(days=i * 3)
    c = novo_cliente(fake.name(), "PF", round(random.uniform(1500, 2500), 2), end, tel, cadastro, "fraude_quadrilha")
    conta = nova_conta(c, cadastro)
    if i < 5:                                           # 5 usando o MESMO celular
        cliente_dispositivo.append({"cliente_id": c["id"], "dispositivo_id": disp_q})
    else:
        cliente_dispositivo.append({"cliente_id": c["id"], "dispositivo_id": disp_q2})
    if i in (3, 5):
        cliente_dispositivo.append({"cliente_id": c["id"], "dispositivo_id": disp_q2})
    quadrilha.append({"cliente": c, "conta": conta})

Q = [q["conta"] for q in quadrilha]

# A1) Golpe do Pix: 14 vítimas mandam dinheiro para a CONTA MULA (Q[1]) ...
mula = Q[1]
vitimas = random.sample(pf_contas, 14)
recebido = 0
t0 = datetime(2026, 10, 10, 9, 0)
for k, v in enumerate(vitimas):
    valor = random.uniform(900, 4800)
    recebido += valor
    tx(v, mula, valor, t0 + timedelta(hours=k * 2, minutes=random.randint(0, 50)))
# ... e a mula repassa ~92% em poucas horas para outras contas da quadrilha
repasse = recebido * 0.92
for k, destino in enumerate([Q[4], Q[5], Q[4]]):
    tx(mula, destino, repasse / 3, t0 + timedelta(hours=30 + k * 3))

# A2) Fracionamento (smurfing): Q[4] manda 6 Pix logo abaixo do limite interno de R$ 10.000 para Q[0]
for k in range(6):
    tx(Q[4], Q[0], random.uniform(9100, 9950), datetime(2026, 10, 14, 10, 0) + timedelta(hours=k * 5))

# A3) Lavagem circular: Q0 -> Q2 -> Q3 -> Q5 -> Q0 (o dinheiro volta para a origem)
valor = 48000
t = datetime(2026, 10, 20, 8, 0)
for o, d in [(Q[0], Q[2]), (Q[2], Q[3]), (Q[3], Q[5]), (Q[5], Q[0])]:
    tx(o, d, valor, t, "TED")
    valor *= random.uniform(0.97, 0.99)
    t += timedelta(hours=random.randint(5, 9))

# ---------------------------------------------------------------------------
# 4) FRAUDE B - grupo menor (3 pessoas) com ciclo rápido de lavagem
# ---------------------------------------------------------------------------
disp_b = novo_dispositivo("iPhone")
tel_b = novo_telefone()
grupo_b = []
for i in range(3):
    cadastro = datetime(2026, 9, 2) + timedelta(days=i)
    c = novo_cliente(fake.name(), "PF", round(random.uniform(1500, 3000), 2), novo_endereco(),
                     tel_b, cadastro, "fraude_grupo_b")
    conta = nova_conta(c, cadastro)
    cliente_dispositivo.append({"cliente_id": c["id"], "dispositivo_id": disp_b})
    grupo_b.append({"cliente": c, "conta": conta})
B = [g["conta"] for g in grupo_b]
t = datetime(2026, 10, 25, 22, 0)
valor = 25000
for o, d in [(B[0], B[1]), (B[1], B[2]), (B[2], B[0])]:
    tx(o, d, valor, t)
    valor *= 0.98
    t += timedelta(hours=3)

transacoes.sort(key=lambda r: r["data_hora"])

# ---------------------------------------------------------------------------
# 5) DOCUMENTOS NÃO ESTRUTURADOS (texto livre) -> entrada do pipeline Text-to-Graph
#    Relatos de vítimas (contestação MED do Pix), notas de analistas e reclamações comuns.
#    Os nomes, contas e telefones aparecem ESCRITOS DE FORMAS DIFERENTES de propósito,
#    para exigir Resolução de Entidades.
# ---------------------------------------------------------------------------
documentos = []
nome_por_cliente = {c["id"]: c["nome"] for c in clientes}
titular_conta = {c["id"]: nome_por_cliente[c["cliente_id"]] for c in contas}
numero_tel = {t["id"]: t["numero"] for t in telefones}


def digitos(s):
    return "".join(ch for ch in s if ch.isdigit())


def variacao_telefone(numero):
    d = digitos(numero)[-11:]
    return random.choice([f"({d[:2]}) {d[2:7]}-{d[7:]}", d, f"+55 {d[:2]} {d[2:7]} {d[7:]}", f"{d[:2]} {d[2:]}"])


def variacao_nome(nome):
    partes = nome.split()
    return random.choice([nome, nome.upper(), f"{partes[0][0]}. {' '.join(partes[1:])}", nome.lower().title()])


def variacao_conta(cid):
    n = cid.split("-")[1]
    return random.choice([cid, f"conta {n}", f"CT{n}", f"conta nº {n}"])


def reais(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def novo_doc(tipo, autor_id, data, titulo, texto):
    documentos.append({"id": f"DOC-{len(documentos) + 1:03d}", "tipo": tipo, "autor_cliente_id": autor_id or "",
                       "data": data, "titulo": titulo, "texto": texto})


golpes = [t for t in transacoes if t["destino"] == mula]
modelos_relato = [
    "Recebi uma ligação do número {tel} de uma pessoa dizendo ser da central de segurança do banco. "
    "Ela falou que minha conta tinha sido invadida e que eu precisava transferir o saldo para uma conta "
    "segura. Fiz um Pix de {valor} para a {conta}, em nome de {nome}. Depois percebi que era golpe.",
    "Caí no golpe do falso funcionário. O contato veio pelo WhatsApp {tel}. Me pediram um Pix de {valor} "
    "para regularizar meu cadastro e o recebedor apareceu como {nome}, {conta}. Peço a devolução pelo MED.",
    "Anunciei um celular na internet e o suposto comprador, que usava o telefone {tel}, me convenceu a fazer "
    "um Pix de {valor} como 'taxa de verificação'. O dinheiro foi para {nome} ({conta}).",
]
for k, t in enumerate(golpes):
    vit_cli = next(c for c in contas if c["id"] == t["origem"])["cliente_id"]
    texto = random.choice(modelos_relato).format(
        tel=variacao_telefone(numero_tel[tel_q]), valor=reais(t["valor"]),
        conta=variacao_conta(mula), nome=variacao_nome(titular_conta[mula]))
    novo_doc("Contestação Pix (MED)", vit_cli, t["data_hora"][:10],
             f"Relato de golpe - {nome_por_cliente[vit_cli]}", texto)

novo_doc("Nota de analista", "", "2026-10-15", "Movimentação atípica abaixo do limite",
         f"A cliente {variacao_nome(titular_conta[Q[4]])} fez seis transferências seguidas entre R$ 9.100 e "
         f"R$ 9.950 para {variacao_nome(titular_conta[Q[0]])} ({variacao_conta(Q[0])}) em 14/10. Os valores "
         f"ficam sempre logo abaixo do nosso limite interno de alerta. Recomendo acompanhar.")
novo_doc("Nota de analista", "", "2026-10-22", "Contas novas com valores altos",
         f"Duas contas abertas em agosto ({variacao_conta(Q[2])} e {variacao_conta(Q[3])}) já "
         f"movimentaram mais de R$ 45 mil cada em um único dia. Titulares: {variacao_nome(titular_conta[Q[2]])} "
         f"e {variacao_nome(titular_conta[Q[3]])}. Nenhuma renda compatível foi declarada.")
novo_doc("Nota de analista", "", "2026-10-27", "Transferências noturnas em sequência",
         f"Entre a noite de 25/10 e a madrugada de 26/10, {variacao_nome(titular_conta[B[0]])}, "
         f"{variacao_nome(titular_conta[B[1]])} e {variacao_nome(titular_conta[B[2]])} trocaram cerca de "
         f"R$ 25 mil entre si. O contato cadastrado dos três é o mesmo: {variacao_telefone(numero_tel[tel_b])}.")

reclamacoes = [
    "Fui cobrado duas vezes pela tarifa do pacote de serviços este mês. Gostaria do estorno.",
    "O aplicativo travou durante um Pix de {valor} para {nome}, mas o valor foi debitado. Confirmar se chegou.",
    "Meu cartão novo ainda não chegou. Já se passaram 15 dias úteis.",
    "Fiz um Pix de {valor} para {nome} por engano, digitei a chave errada. Como peço a devolução?",
    "Não consigo cadastrar minha chave Pix pelo celular. Aparece erro de validação.",
]
for k in range(9):
    autor = random.choice([c for c in clientes if c["perfil_real"] == "legitimo" and c["tipo"] == "PF"])
    destino = random.choice([c for c in clientes if c["perfil_real"] == "legitimo" and c["id"] != autor["id"]])
    texto = reclamacoes[k % len(reclamacoes)].format(valor=reais(random.uniform(50, 900)), nome=destino["nome"])
    novo_doc("Reclamação", autor["id"], momento_aleatorio().date().isoformat(), f"Atendimento - {autor['nome']}", texto)

# ---------------------------------------------------------------------------
# 6) SALVAR
# ---------------------------------------------------------------------------
def salvar(nome, linhas):
    with open(PASTA / f"{nome}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        w.writerows(linhas)


# perfil_real é o "gabarito": NÃO vai para o grafo (o sistema não pode saber a resposta!)
salvar("clientes", [{k: v for k, v in c.items() if k != "perfil_real"} for c in clientes])
salvar("contas", contas)
salvar("enderecos", enderecos)
salvar("telefones", telefones)
salvar("dispositivos", dispositivos)
salvar("cliente_dispositivo", cliente_dispositivo)
salvar("transacoes", transacoes)
with open(PASTA / "documentos.json", "w", encoding="utf-8") as f:
    json.dump(documentos, f, ensure_ascii=False, indent=2)

gabarito = {
    "quadrilha_A": {
        "clientes": [q["cliente"]["nome"] for q in quadrilha],
        "contas": Q,
        "conta_mula": mula,
        "vitimas_do_golpe": len(vitimas),
        "fracionamento": f"{Q[4]} -> {Q[0]} (6 Pix entre R$ 9.100 e R$ 9.950)",
        "ciclo_lavagem": f"{Q[0]} -> {Q[2]} -> {Q[3]} -> {Q[5]} -> {Q[0]}",
    },
    "grupo_B": {
        "clientes": [g["cliente"]["nome"] for g in grupo_b],
        "contas": B,
        "ciclo_lavagem": f"{B[0]} -> {B[1]} -> {B[2]} -> {B[0]}",
    },
}
with open(PASTA / "gabarito.json", "w", encoding="utf-8") as f:
    json.dump(gabarito, f, ensure_ascii=False, indent=2)

print(f"Clientes: {len(clientes)} | Contas: {len(contas)} | Transações: {len(transacoes)}")
print(f"Dispositivos: {len(dispositivos)} | Telefones: {len(telefones)} | Endereços: {len(enderecos)}")
print(f"Documentos de texto: {len(documentos)}")
print(f"Arquivos salvos em: {PASTA}")
