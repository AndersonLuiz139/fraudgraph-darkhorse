"""
Base de CONHECIMENTO do domínio (a parte "K" do KAG).

Aqui ficam:
  - o ESQUEMA do grafo (quais nós e relações existem)
  - as REGRAS de especialistas em fraude, cada uma com sua consulta Cypher
"""

ESQUEMA = """
NÓS:
  (:Cliente {id, nome, tipo: 'PF'|'PJ', documento, idade, renda_mensal, data_cadastro: date})
  (:Conta {id, banco, agencia, tipo, data_abertura: date})
  (:Dispositivo {id, tipo, ip})
  (:Telefone {id, numero})
  (:Endereco {id, logradouro, cidade, uf})

RELAÇÕES:
  (:Cliente)-[:POSSUI]->(:Conta)
  (:Cliente)-[:USA_DISPOSITIVO]->(:Dispositivo)
  (:Cliente)-[:TEM_TELEFONE]->(:Telefone)
  (:Cliente)-[:MORA_EM]->(:Endereco)
  (:Conta)-[:TRANSFERIU {id, valor: float, data_hora: datetime, canal: 'PIX'|'TED'|'CARTAO'}]->(:Conta)
"""

# Limite interno FICTÍCIO usado neste estudo de caso para a regra de fracionamento.
LIMITE_ALERTA = 10000

REGRAS = {
    "R1": {
        "nome": "Identidade compartilhada (anel de fraude)",
        "descricao": "3 ou mais clientes diferentes usando o MESMO telefone, endereço ou dispositivo. "
                     "Duas pessoas no mesmo endereço é normal (família); três ou mais com o mesmo celular "
                     "é forte indício de identidades falsas controladas pela mesma pessoa.",
        "cypher": """
MATCH (c:Cliente)-[:USA_DISPOSITIVO|TEM_TELEFONE|MORA_EM]->(x)
WITH x, labels(x)[0] AS tipo, collect(DISTINCT c.nome) AS clientes
WHERE size(clientes) >= 3
RETURN tipo, coalesce(x.numero, x.ip, x.logradouro) AS identificador,
       size(clientes) AS qtd_clientes, clientes
ORDER BY qtd_clientes DESC
""",
    },
    "R2": {
        "nome": "Ciclo de lavagem (dinheiro que volta para a origem)",
        "descricao": "Valores altos (>= R$ 20.000) que passam por 3 a 5 contas em sequência e retornam à "
                     "conta de origem em até 72 horas. Serve para 'esquentar' dinheiro e simular movimentação.",
        "cypher": """
MATCH p = (a:Conta)-[:TRANSFERIU*3..5]->(a)
WITH p, a, relationships(p) AS ts, [n IN nodes(p) | n.id] AS ids
WHERE all(t IN ts WHERE t.valor >= 20000)
  AND all(i IN range(0, size(ts) - 2) WHERE ts[i].data_hora < ts[i + 1].data_hora)
  AND duration.inSeconds(ts[0].data_hora, ts[-1].data_hora).hours <= 72
  AND a.id = reduce(m = ids[0], x IN ids | CASE WHEN x < m THEN x ELSE m END)
UNWIND nodes(p) AS n
MATCH (cli:Cliente)-[:POSSUI]->(n)
WITH p, ts, collect(DISTINCT cli.nome) AS clientes
RETURN [n IN nodes(p) | n.id] AS contas, size(ts) AS saltos,
       round(ts[0].valor, 2) AS valor_inicial, round(ts[-1].valor, 2) AS valor_final,
       duration.inSeconds(ts[0].data_hora, ts[-1].data_hora).hours AS horas, clientes
""",
    },
    "R3": {
        "nome": "Conta mula (recebe de muitos e repassa quase tudo)",
        "descricao": "Conta aberta há pouco tempo (após 2026-06-01) que recebe de 8 ou mais contas "
                     "diferentes e repassa 80% ou mais do que recebeu. Lojas também recebem de muitos, "
                     "mas não repassam quase tudo — é isso que diferencia.",
        "cypher": """
MATCH (o:Conta)-[e:TRANSFERIU]->(m:Conta)
WHERE m.data_abertura >= date('2026-06-01')
WITH m, count(DISTINCT o) AS origens, sum(e.valor) AS recebido
WHERE origens >= 8
MATCH (m)-[s:TRANSFERIU]->(d:Conta)
WITH m, origens, recebido, sum(s.valor) AS enviado, collect(DISTINCT d.id) AS destinos
WHERE enviado >= 0.8 * recebido
MATCH (cli:Cliente)-[:POSSUI]->(m)
RETURN m.id AS conta, cli.nome AS titular, origens, round(recebido, 2) AS recebido,
       round(enviado, 2) AS repassado, round(100 * enviado / recebido, 1) AS percentual_repassado,
       destinos, [cli.nome] AS clientes
""",
    },
    "R4": {
        "nome": "Fracionamento (smurfing)",
        "descricao": f"4 ou mais transferências da mesma origem para o mesmo destino com valores entre "
                     "90% e 100% do limite interno de alerta (R$ 10.000). O fraudador divide "
                     "o valor para não disparar o alerta.",
        "cypher": f"""
MATCH (a:Conta)-[t:TRANSFERIU]->(b:Conta)
WHERE t.valor >= {LIMITE_ALERTA * 0.9} AND t.valor < {LIMITE_ALERTA}
WITH a, b, count(t) AS qtd, sum(t.valor) AS total
WHERE qtd >= 4
MATCH (ca:Cliente)-[:POSSUI]->(a), (cb:Cliente)-[:POSSUI]->(b)
RETURN a.id AS origem, ca.nome AS titular_origem, b.id AS destino, cb.nome AS titular_destino,
       qtd, round(total, 2) AS total, [ca.nome, cb.nome] AS clientes
""",
    },
}

PERGUNTAS_PRONTAS = {
    "Existem clientes compartilhando telefone, endereço ou celular?": ["R1"],
    "Há dinheiro circulando em ciclo entre contas (lavagem)?": ["R2"],
    "Existe alguma conta mula recebendo dinheiro de golpes?": ["R3"],
    "Alguém está fracionando transferências para fugir do limite?": ["R4"],
    "Os alertas estão ligados entre si? Existe uma quadrilha?": ["R1", "R2", "R3", "R4"],
}


def texto_regras():
    return "\n".join(f"{k} - {r['nome']}: {r['descricao']}" for k, r in REGRAS.items())
