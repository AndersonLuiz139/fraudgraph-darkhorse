"""
PILAR 1 - Pipeline Text-to-Graph
================================
Lê documentos de texto livre (relatos de golpe, notas de analistas, reclamações) e:

  1. NER  - reconhece entidades: Pessoa, Conta, Telefone, Valor, Data
  2. RE   - extrai relações: (vítima)-[:RELATOU_GOLPE]->(conta), (documento)-[:MENCIONA]->(entidade)
  3. ER   - Resolução de Entidades: "D. Vasconcelos", "DOM VASCONCELOS" e "Dom Vasconcelos"
            viram UM ÚNICO nó canônico (CLI-0162). Telefones e contas escritos de jeitos
            diferentes também são normalizados.

Dois extratores:
  - ExtratorRegras: expressões regulares + dicionário de nomes (funciona sem internet/LLM)
  - ExtratorLLM:    o LLM devolve as entidades em JSON (usa o mesmo resolvedor no final)

Uso:
    python extracao.py            (usa LLM se configurado no .env, senão regras)
    python extracao.py --regras   (força o extrator por regras)
"""
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

PASTA = Path(__file__).parent / "dados"

RE_VALOR = re.compile(r"R\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?")
RE_TELEFONE = re.compile(r"(?:\+55\s?)?\(?\d{2}\)?\s?\d{4,5}[-\s]?\d{4}")
RE_CONTA = re.compile(r"\b(?:CT-?|conta(?: n[ºo°])?\s)(\d{4})\b", re.I)
_TOKEN = r"(?:[A-ZÀ-Ý]\.|[A-ZÀ-Ý][\wÀ-ÿ]*)"
RE_NOME = re.compile(rf"\b{_TOKEN}(?:\s+(?:d[aeo]s?\s+)?{_TOKEN}){{1,4}}")
PALAVRAS_IGNORADAS = {"pix", "med", "whatsapp", "ted", "peço", "fiz", "depois", "caí", "anunciei", "recebi"}


# ---------------------------------------------------------------------------
# Normalização (base da Resolução de Entidades)
# ---------------------------------------------------------------------------
def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalizar_nome(s: str) -> str:
    return re.sub(r"\s+", " ", sem_acento(s).lower().replace(".", "")).strip()


def normalizar_telefone(s: str) -> str:
    d = re.sub(r"\D", "", s)
    if len(d) > 11 and d.startswith("55"):
        d = d[2:]
    return d.lstrip("0")


def normalizar_conta(numero: str) -> str:
    return f"CT-{int(numero):04d}"


def valor_float(s: str) -> float:
    return float(s.replace("R$", "").strip().replace(".", "").replace(",", "."))


class ResolvedorEntidades:
    """Liga cada menção encontrada no texto a um nó canônico do grafo."""

    def __init__(self, pasta=PASTA, limiar=0.86):
        self.cli = pd.read_csv(pasta / "clientes.csv", dtype=str)
        tel = pd.read_csv(pasta / "telefones.csv", dtype=str)
        self.contas = set(pd.read_csv(pasta / "contas.csv", dtype=str)["id"])
        self.tel_por_numero = {normalizar_telefone(n): i for i, n in zip(tel["id"], tel["numero"])}
        self.nomes = [(cid, nome, normalizar_nome(nome)) for cid, nome in zip(self.cli["id"], self.cli["nome"])]
        self.limiar = limiar

    def pessoa(self, texto: str):
        alvo = normalizar_nome(texto)
        partes = alvo.split()
        melhor = (None, None, 0.0)
        for cid, nome, norm in self.nomes:
            if len(partes) >= 2 and len(partes[0]) == 1:  # forma abreviada: "D. Vasconcelos"
                np_ = norm.split()
                score = 0.95 if np_[0][0] == partes[0] and " ".join(np_[1:]) == " ".join(partes[1:]) else 0
            else:
                score = SequenceMatcher(None, alvo, norm).ratio()
            if score > melhor[2]:
                melhor = (cid, nome, score)
        return melhor if melhor[2] >= self.limiar else (None, None, melhor[2])

    def telefone(self, texto: str):
        n = normalizar_telefone(texto)
        return (self.tel_por_numero.get(n), n)

    def conta(self, numero: str):
        cid = normalizar_conta(numero)
        return cid if cid in self.contas else None


# ---------------------------------------------------------------------------
# Extratores (NER + RE)
# ---------------------------------------------------------------------------
class ExtratorRegras:
    nome = "Regras (regex + dicionário)"

    def entidades(self, texto: str) -> dict:
        sem_tel = RE_TELEFONE.sub(" ", texto)
        nomes = [m.group(0) for m in RE_NOME.finditer(sem_tel)
                 if m.group(0).split()[0].lower() not in PALAVRAS_IGNORADAS]
        return {"pessoas": nomes,
                "contas": RE_CONTA.findall(texto),
                "telefones": RE_TELEFONE.findall(texto),
                "valores": RE_VALOR.findall(texto)}


class ExtratorLLM:
    nome = "LLM (JSON estruturado)"

    def entidades(self, texto: str) -> dict:
        from llm import _chat
        sistema = ("Você é um extrator de entidades (NER) para documentos bancários em português. "
                   "Devolva SOMENTE um JSON com as chaves: pessoas (nomes de pessoas exatamente como aparecem), "
                   "contas (apenas os 4 dígitos do número da conta), telefones (como aparecem), "
                   "valores (como aparecem, ex.: 'R$ 1.234,56'). Não invente nada que não esteja no texto.")
        bruto = _chat(sistema, texto, 0)
        m = re.search(r"\{.*\}", bruto, re.S)
        dados = json.loads(m.group(0))
        dados["contas"] = [re.sub(r"\D", "", str(c))[-4:] for c in dados.get("contas", []) if re.search(r"\d", str(c))]
        return {k: list(dados.get(k, [])) for k in ("pessoas", "contas", "telefones", "valores")}


def processar_documento(doc: dict, extrator, resolvedor: ResolvedorEntidades) -> dict:
    """NER -> ER -> RE para um documento."""
    ents = extrator.entidades(doc["texto"])
    mencoes, relacoes = [], []

    for p in ents["pessoas"]:
        cid, canon, score = resolvedor.pessoa(p)
        mencoes.append({"tipo": "Cliente", "texto": p, "id": cid, "canonico": canon, "score": round(score, 2)})
    for c in ents["contas"]:
        mencoes.append({"tipo": "Conta", "texto": c, "id": resolvedor.conta(c), "canonico": normalizar_conta(c),
                        "score": 1.0})
    for t in ents["telefones"]:
        tid, norm = resolvedor.telefone(t)
        mencoes.append({"tipo": "Telefone", "texto": t, "id": tid, "canonico": norm, "score": 1.0 if tid else 0})

    valores = [valor_float(v) for v in ents["valores"]]

    # RE: em uma contestação de golpe, o autor é a vítima, a conta citada é a recebedora
    # do golpe e o telefone citado é o contato do golpista.
    if doc["tipo"].startswith("Contestação") and doc.get("autor_cliente_id"):
        for m in mencoes:
            if m["tipo"] == "Conta" and m["id"]:
                relacoes.append({"origem": doc["autor_cliente_id"], "tipo": "RELATOU_GOLPE", "destino": m["id"],
                                 "valor": valores[0] if valores else None, "data": doc["data"], "doc": doc["id"]})
            if m["tipo"] == "Telefone":
                m["papel"] = "contato_golpista"
            if m["tipo"] in ("Cliente", "Conta"):
                m["papel"] = "recebedor_golpe"
    for m in mencoes:
        m.setdefault("papel", "mencionado")

    # deduplica menções que resolveram para o mesmo nó
    vistos, unicas = set(), []
    for m in mencoes:
        chave = (m["tipo"], m["id"] or m["canonico"] or m["texto"])
        if chave not in vistos:
            vistos.add(chave)
            unicas.append(m)
    return {**doc, "mencoes": unicas, "relacoes": relacoes, "valores": valores}


def rodar_pipeline(usar_llm=None) -> dict:
    from llm import llm_configurado
    docs = json.loads((PASTA / "documentos.json").read_text(encoding="utf-8"))
    resolvedor = ResolvedorEntidades()
    usar_llm = llm_configurado() if usar_llm is None else usar_llm
    extrator = ExtratorLLM() if usar_llm else ExtratorRegras()
    saida = []
    for d in docs:
        try:
            saida.append(processar_documento(d, extrator, resolvedor))
        except Exception as e:  # se o LLM falhar em um documento, cai para regras
            print(f"  {d['id']}: LLM falhou ({e}); usando regras.")
            saida.append(processar_documento(d, ExtratorRegras(), resolvedor))

    todas = [m for d in saida for m in d["mencoes"]]
    textos_pessoa = {m["texto"] for m in todas if m["tipo"] == "Cliente"}
    ids_pessoa = {m["id"] for m in todas if m["tipo"] == "Cliente" and m["id"]}
    est = {
        "extrator": extrator.nome,
        "documentos": len(saida),
        "mencoes": len(todas),
        "resolvidas": sum(1 for m in todas if m["id"]),
        "nao_resolvidas": sum(1 for m in todas if not m["id"]),
        "grafias_diferentes_de_pessoas": len(textos_pessoa),
        "pessoas_canonicas": len(ids_pessoa),
        "relacoes_extraidas": sum(len(d["relacoes"]) for d in saida),
    }
    resultado = {"estatisticas": est, "documentos": saida}
    (PASTA / "extracao.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    return resultado


if __name__ == "__main__":
    r = rodar_pipeline(usar_llm=False if "--regras" in sys.argv else None)
    for k, v in r["estatisticas"].items():
        print(f"{k:32s} {v}")
    print("Salvo em dados/extracao.json")
