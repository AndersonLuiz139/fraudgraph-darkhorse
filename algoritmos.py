"""
Algoritmos de grafo usados para ENRIQUECER o contexto do LLM:

  - PageRank (contas): mede a "centralidade" do dinheiro. Contas para onde muito dinheiro converge
                       (direta ou indiretamente) ficam com PageRank alto.
  - Louvain (clientes): detecção de comunidades. Agrupa clientes muito ligados entre si
                        (identificadores compartilhados + transferências).
  - Caminho mais curto: feito direto no Cypher com shortestPath() na hora da pergunta.

Calculamos com NetworkX e gravamos o resultado como propriedade no Neo4j (n.pagerank, n.comunidade).
Assim funciona também no Neo4j Aura Free, que não tem o plugin GDS.
(Com o plugin GDS instalado, o equivalente seria gds.pageRank.write e gds.louvain.write.)
"""
from pathlib import Path

import networkx as nx
import pandas as pd

PASTA = Path(__file__).parent / "dados"


def calcular(pasta=PASTA) -> dict:
    ler = lambda n: pd.read_csv(pasta / f"{n}.csv", dtype=str)
    cli, ct, cd, tx = ler("clientes"), ler("contas"), ler("cliente_dispositivo"), ler("transacoes")
    tx["valor"] = tx["valor"].astype(float)

    # PageRank ponderado pelo valor transferido
    g_tx = nx.DiGraph()
    g_tx.add_nodes_from(ct["id"])
    for (o, d), v in tx.groupby(["origem", "destino"])["valor"].sum().items():
        g_tx.add_edge(o, d, weight=v)
    pr = nx.pagerank(g_tx, weight="weight")

    # Louvain em um grafo de clientes
    dono = dict(zip(ct["id"], ct["cliente_id"]))
    g_cli = nx.Graph()
    g_cli.add_nodes_from(cli["id"])
    ident = pd.concat([cli[["id", "telefone_id"]].rename(columns={"telefone_id": "x"}),
                       cli[["id", "endereco_id"]].rename(columns={"endereco_id": "x"}),
                       cd.rename(columns={"cliente_id": "id", "dispositivo_id": "x"})])
    for _, grupo in ident.groupby("x")["id"]:
        membros = sorted(set(grupo))
        for i, a in enumerate(membros):
            for b in membros[i + 1:]:
                w = g_cli[a][b]["weight"] + 3 if g_cli.has_edge(a, b) else 3
                g_cli.add_edge(a, b, weight=w)
    for t in tx.itertuples():
        a, b = dono[t.origem], dono[t.destino]
        if a != b:
            w = g_cli[a][b]["weight"] + 1 if g_cli.has_edge(a, b) else 1
            g_cli.add_edge(a, b, weight=w)
    comunidades = nx.community.louvain_communities(g_cli, weight="weight", seed=42)
    com = {n: i for i, c in enumerate(sorted(comunidades, key=len, reverse=True)) for n in c}

    return {"pagerank_conta": pr, "comunidade_cliente": com,
            "comunidade_conta": {c: com[dono[c]] for c in ct["id"]}}


if __name__ == "__main__":
    r = calcular()
    top = sorted(r["pagerank_conta"].items(), key=lambda x: -x[1])[:5]
    print("Top PageRank:", [(c, round(v, 4)) for c, v in top])
    print("Comunidades:", len(set(r["comunidade_cliente"].values())))
