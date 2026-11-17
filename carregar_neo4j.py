"""
PILAR 2 - Modelagem LPG no Neo4j + ingestão
===========================================
1. Cria CONSTRAINTS de unicidade e ÍNDICES (Range/BTree, Fulltext e Vetorial)
2. Carrega os dados estruturados (CSV) e o resultado da extração de texto (extracao.json)
3. Grava os embeddings dos documentos (para o índice vetorial)
4. Calcula PageRank e Louvain e grava como propriedades

Uso:
    python carregar_neo4j.py          (lê NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD do .env)
"""
import json
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase

import algoritmos

load_dotenv()
PASTA = Path(__file__).parent / "dados"

SCHEMA = [
    # --- Constraints de unicidade (também criam índice automaticamente) ---
    "CREATE CONSTRAINT cliente_id IF NOT EXISTS FOR (n:Cliente) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT conta_id IF NOT EXISTS FOR (n:Conta) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT dispositivo_id IF NOT EXISTS FOR (n:Dispositivo) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT telefone_id IF NOT EXISTS FOR (n:Telefone) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT endereco_id IF NOT EXISTS FOR (n:Endereco) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT documento_id IF NOT EXISTS FOR (n:Documento) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT transferencia_id IF NOT EXISTS FOR ()-[t:TRANSFERIU]-() REQUIRE t.id IS UNIQUE",
    # --- Índices Range (B-Tree) para filtros por propriedade ---
    "CREATE INDEX cliente_documento IF NOT EXISTS FOR (n:Cliente) ON (n.documento)",
    "CREATE INDEX telefone_numero IF NOT EXISTS FOR (n:Telefone) ON (n.numero_normalizado)",
    "CREATE INDEX conta_abertura IF NOT EXISTS FOR (n:Conta) ON (n.data_abertura)",
    "CREATE INDEX transferencia_valor IF NOT EXISTS FOR ()-[t:TRANSFERIU]-() ON (t.valor)",
    "CREATE INDEX transferencia_data IF NOT EXISTS FOR ()-[t:TRANSFERIU]-() ON (t.data_hora)",
    # --- Índices Fulltext (busca por palavras) ---
    "CREATE FULLTEXT INDEX doc_texto IF NOT EXISTS FOR (n:Documento) ON EACH [n.titulo, n.texto]",
    "CREATE FULLTEXT INDEX cliente_nome IF NOT EXISTS FOR (n:Cliente) ON EACH [n.nome]",
]


def criar_indice_vetorial(s, dimensoes):
    s.run("DROP INDEX doc_embedding IF EXISTS")
    s.run(f"""CREATE VECTOR INDEX doc_embedding IF NOT EXISTS FOR (d:Documento) ON (d.embedding)
              OPTIONS {{indexConfig: {{`vector.dimensions`: {dimensoes}, `vector.similarity_function`: 'cosine'}}}}""")


def lotes(df_ou_lista, n=500):
    linhas = df_ou_lista.to_dict("records") if isinstance(df_ou_lista, pd.DataFrame) else df_ou_lista
    for i in range(0, len(linhas), n):
        yield linhas[i:i + n]


def carregar(uri, usuario, senha, limpar=True):
    from extracao import normalizar_telefone
    ler = lambda n: pd.read_csv(PASTA / f"{n}.csv", dtype=str).fillna("")
    cli, ct, end, tel = ler("clientes"), ler("contas"), ler("enderecos"), ler("telefones")
    disp, cd, tx = ler("dispositivos"), ler("cliente_dispositivo"), ler("transacoes")
    tel["numero_normalizado"] = tel["numero"].map(normalizar_telefone)
    extr = json.loads((PASTA / "extracao.json").read_text(encoding="utf-8"))
    emb = json.loads((PASTA / "embeddings.json").read_text())
    alg = algoritmos.calcular()

    driver = GraphDatabase.driver(uri, auth=(usuario, senha))
    with driver.session() as s:
        if limpar:
            print("Limpando o banco...")
            s.run("MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 2000 ROWS")
        print("Criando constraints e índices...")
        for q in SCHEMA:
            s.run(q)
        criar_indice_vetorial(s, len(next(iter(emb.values()))))

        print("Carregando nós...")
        for b in lotes(end):
            s.run("UNWIND $b AS r MERGE (n:Endereco {id: r.id}) SET n.logradouro = r.logradouro, n.cidade = r.cidade, n.uf = r.uf", b=b)
        for b in lotes(tel):
            s.run("UNWIND $b AS r MERGE (n:Telefone {id: r.id}) SET n.numero = r.numero, n.numero_normalizado = r.numero_normalizado", b=b)
        for b in lotes(disp):
            s.run("UNWIND $b AS r MERGE (n:Dispositivo {id: r.id}) SET n.tipo = r.tipo, n.ip = r.ip", b=b)
        for b in lotes(cli):
            s.run("""UNWIND $b AS r MERGE (c:Cliente {id: r.id})
                     SET c.nome = r.nome, c.tipo = r.tipo, c.documento = r.documento,
                         c.idade = CASE r.idade WHEN '' THEN null ELSE toInteger(r.idade) END,
                         c.renda_mensal = toFloat(r.renda_mensal), c.data_cadastro = date(r.data_cadastro)
                     WITH c, r
                     MATCH (t:Telefone {id: r.telefone_id}), (e:Endereco {id: r.endereco_id})
                     MERGE (c)-[:TEM_TELEFONE]->(t) MERGE (c)-[:MORA_EM]->(e)""", b=b)
        for b in lotes(cd):
            s.run("""UNWIND $b AS r MATCH (c:Cliente {id: r.cliente_id}), (d:Dispositivo {id: r.dispositivo_id})
                     MERGE (c)-[:USA_DISPOSITIVO]->(d)""", b=b)
        for b in lotes(ct):
            s.run("""UNWIND $b AS r MATCH (cli:Cliente {id: r.cliente_id})
                     MERGE (c:Conta {id: r.id})
                     SET c.banco = r.banco, c.agencia = r.agencia, c.tipo = r.tipo, c.data_abertura = date(r.data_abertura)
                     MERGE (cli)-[:POSSUI]->(c)""", b=b)

        print("Carregando transações...")
        for b in lotes(tx, 1000):
            s.run("""UNWIND $b AS r MATCH (o:Conta {id: r.origem}), (d:Conta {id: r.destino})
                     MERGE (o)-[t:TRANSFERIU {id: r.id}]->(d)
                     SET t.valor = toFloat(r.valor), t.data_hora = datetime(r.data_hora), t.canal = r.canal""", b=b)

        print("Carregando documentos extraídos (Text-to-Graph)...")
        for d in extr["documentos"]:
            s.run("""MERGE (doc:Documento {id: $id})
                     SET doc.tipo = $tipo, doc.titulo = $titulo, doc.texto = $texto, doc.data = date($data),
                         doc.embedding = $emb""",
                  id=d["id"], tipo=d["tipo"], titulo=d["titulo"], texto=d["texto"], data=d["data"], emb=emb[d["id"]])
            for m in d["mencoes"]:
                if m["id"]:
                    s.run("""MATCH (doc:Documento {id: $doc}), (e {id: $eid})
                             MERGE (doc)-[r:MENCIONA]->(e)
                             SET r.texto_original = $txt, r.papel = $papel, r.score_resolucao = $score""",
                          doc=d["id"], eid=m["id"], txt=m["texto"], papel=m["papel"], score=m["score"])
            for r in d["relacoes"]:
                s.run("""MATCH (v:Cliente {id: $o}), (c:Conta {id: $d})
                         MERGE (v)-[g:RELATOU_GOLPE {doc: $doc}]->(c) SET g.valor = $valor, g.data = date($data)""",
                      o=r["origem"], d=r["destino"], doc=r["doc"], valor=r["valor"], data=r["data"])

        print("Gravando PageRank e comunidades (Louvain)...")
        s.run("UNWIND $b AS r MATCH (c:Conta {id: r.id}) SET c.pagerank = r.pr, c.comunidade = r.com",
              b=[{"id": k, "pr": v, "com": alg["comunidade_conta"][k]} for k, v in alg["pagerank_conta"].items()])
        s.run("UNWIND $b AS r MATCH (c:Cliente {id: r.id}) SET c.comunidade = r.com",
              b=[{"id": k, "com": v} for k, v in alg["comunidade_cliente"].items()])
        s.run("CALL db.awaitIndexes(300)")

        n = s.run("MATCH (n) RETURN count(n) AS n").single()["n"]
        r = s.run("MATCH ()-[r]->() RETURN count(r) AS r").single()["r"]
    driver.close()
    print(f"Pronto! {n} nós e {r} relações no Neo4j.")


if __name__ == "__main__":
    carregar(os.getenv("NEO4J_URI", "bolt://localhost:7687"), os.getenv("NEO4J_USER", "neo4j"),
             os.getenv("NEO4J_PASSWORD", "senha12345"))
