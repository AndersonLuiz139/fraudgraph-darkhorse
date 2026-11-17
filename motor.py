"""
PILAR 3 - Recuperação Híbrida (Vetor + Cypher) e travessia Multi-hop
=====================================================================
Dois "motores" com a MESMA interface:
  - MotorNeo4j   -> modo principal: índices vetorial/fulltext + Cypher no Neo4j
  - MotorOffline -> plano B: o MESMO grafo montado em memória com NetworkX (sem Neo4j)

Fluxo de uma pergunta (recuperar_contexto):
  1. Busca VETORIAL nos documentos (similaridade de cosseno)
  2. Entidades citadas nesses documentos + nomes/contas citados na pergunta = SEMENTES
  3. Travessia MULTI-HOP a partir das sementes (Cliente -> Telefone <- outro Cliente -> Conta -> ...)
  4. Enriquecimento com algoritmos de grafo (PageRank, comunidade Louvain)
  5. Regras de especialista (KAG) aplicadas ao subgrafo
"""
import json
import re
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from conhecimento import LIMITE_ALERTA, REGRAS

PASTA = Path(__file__).parent / "dados"
PALAVRAS_PROIBIDAS = ("CREATE", "MERGE", "DELETE", "DETACH", "SET ", "REMOVE", "DROP", "LOAD CSV", "DBMS", "APOC")


def cypher_somente_leitura(q: str) -> bool:
    return not any(p in q.upper() for p in PALAVRAS_PROIBIDAS)


def rotulo_no(tipo, props):
    return str(props.get("nome") or props.get("numero") or props.get("logradouro") or props.get("ip")
               or props.get("titulo") or props.get("id"))


# ---------------------------------------------------------------------------
# Lógica comum aos dois motores
# ---------------------------------------------------------------------------
class MotorBase:
    def sementes_da_pergunta(self, pergunta: str) -> list:
        from extracao import RE_CONTA, RE_NOME, ResolvedorEntidades, normalizar_conta
        res = ResolvedorEntidades()
        ids = [normalizar_conta(n) for n in RE_CONTA.findall(pergunta)]
        for m in RE_NOME.finditer(pergunta):
            cid, _, _ = res.pessoa(m.group(0))
            if cid:
                ids.append(cid)
        return ids

    def agrupar_quadrilhas(self, resultados: dict) -> list:
        """Une clientes que aparecem JUNTOS em algum alerta -> alertas isolados viram UMA rede."""
        g, regras_por = nx.Graph(), {}
        for codigo, df in resultados.items():
            if df is None or df.empty or "clientes" not in df.columns:
                continue
            for lista in df["clientes"]:
                lista = list(lista)
                for n in lista:
                    g.add_node(n)
                    regras_por.setdefault(n, set()).add(codigo)
                g.add_edges_from(zip(lista, lista[1:]))
        grupos = [{"clientes": sorted(c), "qtd_clientes": len(c),
                   "regras_disparadas": sorted(set().union(*(regras_por[n] for n in c)))}
                  for c in nx.connected_components(g)]
        return sorted(grupos, key=lambda x: (-len(x["regras_disparadas"]), -x["qtd_clientes"]))

    def todas_regras(self) -> dict:
        return {c: self.regra(c) for c in REGRAS}

    def recuperar_contexto(self, pergunta: str, regras: list = None, k_docs=4, saltos=2) -> dict:
        trilha_cypher = []
        docs = self.busca_vetorial(pergunta, k_docs)
        trilha_cypher.append(self.CYPHER_VETOR)
        sementes = self.sementes_da_pergunta(pergunta)
        for d in docs:
            if d["score"] >= 0.25:
                sementes += d["entidades"]
        regras = regras or []
        resultados = {c: self.regra(c) for c in regras}
        for c in regras:
            trilha_cypher.append(REGRAS[c]["cypher"].strip())
        nomes_alerta = {n for df in resultados.values() if not df.empty for lst in df["clientes"] for n in lst}
        sementes += self.ids_por_nome(sorted(nomes_alerta))
        sementes = list(dict.fromkeys(s for s in sementes if s))
        sub, cy = self.expandir(sementes, saltos)
        trilha_cypher.append(cy)
        return {"documentos": docs, "sementes": sementes, "subgrafo": sub, "regras": resultados,
                "quadrilhas": self.agrupar_quadrilhas(resultados) if len(regras) > 1 else [],
                "cypher": trilha_cypher}

    @staticmethod
    def caminhos_texto(g: nx.MultiDiGraph, limite=120) -> list:
        linhas = []
        for o, d, e in g.edges(data=True):
            if e.get("tipo") == "TRANSFERIU":
                extra = f" {{valor: R$ {e['valor']:,.2f}, data: {e['data_hora']}}}"
            elif e.get("tipo") == "RELATOU_GOLPE":
                extra = f" {{valor: R$ {e.get('valor') or 0:,.2f}, doc: {e.get('doc')}}}"
            else:
                extra = ""
            no = lambda n: f"({g.nodes[n]['tipo']}:{g.nodes[n]['rotulo']} [{n}])"
            linhas.append(f"{no(o)}-[:{e.get('tipo')}{extra}]->{no(d)}")
        return linhas[:limite]

    @staticmethod
    def confianca(ctx: dict) -> dict:
        """Nível de confiança da resposta = quanta evidência INDEPENDENTE o grafo trouxe."""
        sim = max([d["score"] for d in ctx["documentos"]] or [0])
        regras_com_achado = sum(1 for df in ctx["regras"].values() if not df.empty)
        arestas = ctx["subgrafo"].number_of_edges()
        pontos = 0.35 * min(sim / 0.6, 1) + 0.4 * min(regras_com_achado / 2, 1) + 0.25 * min(arestas / 10, 1)
        nivel = "Alta" if pontos >= 0.7 else "Média" if pontos >= 0.4 else "Baixa"
        return {"nivel": nivel, "pontuacao": round(pontos, 2),
                "detalhe": f"similaridade máx. dos documentos = {sim:.2f}; regras com achados = "
                           f"{regras_com_achado}; relações no subgrafo = {arestas}"}


# ---------------------------------------------------------------------------
# Motor Neo4j
# ---------------------------------------------------------------------------
class MotorNeo4j(MotorBase):
    nome = "Neo4j"
    CYPHER_VETOR = ("CALL db.index.vector.queryNodes('doc_embedding', $k, $vetor) YIELD node, score\n"
                    "OPTIONAL MATCH (node)-[:MENCIONA]->(e) RETURN node, score, collect(e.id) AS entidades")
    CYPHER_EXPANDIR = """
MATCH (s) WHERE s.id IN $ids AND NOT s:Documento
CALL {
  WITH s
  MATCH p = (s)-[:POSSUI|USA_DISPOSITIVO|TEM_TELEFONE|MORA_EM*1..2]-(x)
  RETURN p LIMIT 60
  UNION
  WITH s
  MATCH p = (s)-[:POSSUI*0..1]->(:Conta)<-[:RELATOU_GOLPE]-(:Cliente)
  RETURN p
  UNION
  WITH s
  MATCH (s)-[:POSSUI*0..1]->(c:Conta)-[t:TRANSFERIU]-(o:Conta)
  WITH c, t, o ORDER BY t.valor DESC LIMIT 25
  MATCH p = (c)-[t]-(o)<-[:POSSUI]-(:Cliente)
  RETURN p
}
RETURN p"""

    def __init__(self, uri, usuario, senha):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(usuario, senha))
        self.driver.verify_connectivity()

    def consultar(self, cypher: str, **params) -> pd.DataFrame:
        if not cypher_somente_leitura(cypher):
            raise ValueError("Por segurança, só consultas de LEITURA são permitidas.")
        with self.driver.session() as s:
            return pd.DataFrame([r.data() for r in s.run(cypher, **params)])

    def estatisticas(self) -> dict:
        q = """
        CALL { MATCH (c:Cliente) RETURN count(c) AS clientes }
        CALL { MATCH (c:Conta) RETURN count(c) AS contas }
        CALL { MATCH ()-[t:TRANSFERIU]->() RETURN count(t) AS transacoes, sum(t.valor) AS volume }
        CALL { MATCH (d:Documento) RETURN count(d) AS documentos }
        CALL { MATCH ()-[r]->() RETURN count(r) AS relacoes }
        RETURN clientes, contas, transacoes, volume, documentos, relacoes"""
        return self.consultar(q).iloc[0].to_dict()

    def regra(self, codigo):
        return self.consultar(REGRAS[codigo]["cypher"])

    def nomes_clientes(self):
        return self.consultar("MATCH (c:Cliente) RETURN c.nome AS nome ORDER BY nome")["nome"].tolist()

    def ids_por_nome(self, nomes):
        if not nomes:
            return []
        return self.consultar("MATCH (c:Cliente) WHERE c.nome IN $n RETURN c.id AS id", n=nomes)["id"].tolist()

    def busca_vetorial(self, pergunta, k=4):
        from embeddings import embedding_consulta
        vet = embedding_consulta(pergunta).tolist()
        with self.driver.session() as s:
            res = s.run(self.CYPHER_VETOR, k=k, vetor=vet)
            return [{"id": r["node"]["id"], "titulo": r["node"]["titulo"], "texto": r["node"]["texto"],
                     "tipo": r["node"]["tipo"], "score": float(r["score"]), "entidades": r["entidades"]} for r in res]

    def busca_textual(self, termo, k=5):
        q = ("CALL db.index.fulltext.queryNodes('doc_texto', $t) YIELD node, score "
             "RETURN node.id AS id, node.titulo AS titulo, node.texto AS texto, score LIMIT $k")
        return self.consultar(q, t=termo, k=k)

    def _caminhos_para_grafo(self, caminhos, g=None):
        g = g or nx.MultiDiGraph()
        for p in caminhos:
            for n in p.nodes:
                tipo = next(iter(n.labels))
                g.add_node(n["id"], tipo=tipo, rotulo=rotulo_no(tipo, dict(n)), **{k: v for k, v in dict(n).items()
                           if k in ("pagerank", "comunidade")})
            for r in p.relationships:
                props = dict(r)
                if "data_hora" in props:
                    props["data_hora"] = str(props["data_hora"])[:16]
                chave = props.get("id") or r.type
                if not g.has_edge(r.start_node["id"], r.end_node["id"], key=chave):
                    g.add_edge(r.start_node["id"], r.end_node["id"], key=chave, tipo=r.type, **props)
        return g

    def expandir(self, ids, saltos=2):
        with self.driver.session() as s:
            caminhos = [r["p"] for r in s.run(self.CYPHER_EXPANDIR, ids=ids)]
        return self._caminhos_para_grafo(caminhos), self.CYPHER_EXPANDIR.strip()

    def caminho_mais_curto(self, id_a, id_b):
        q = """MATCH (a {id: $a}), (b {id: $b})
MATCH p = shortestPath((a)-[:POSSUI|USA_DISPOSITIVO|TEM_TELEFONE|MORA_EM|TRANSFERIU|RELATOU_GOLPE*..8]-(b))
RETURN p"""
        with self.driver.session() as s:
            caminhos = [r["p"] for r in s.run(q, a=id_a, b=id_b)]
        return self._caminhos_para_grafo(caminhos), q

    def ranking_pagerank(self, n=10):
        return self.consultar("""MATCH (c:Conta)<-[:POSSUI]-(cli:Cliente)
RETURN c.id AS conta, cli.nome AS titular, round(c.pagerank, 4) AS pagerank, c.comunidade AS comunidade
ORDER BY pagerank DESC LIMIT $n""", n=n)

    def comunidades(self):
        return self.consultar("""MATCH (c:Cliente) WITH c.comunidade AS comunidade, collect(c.nome) AS membros
RETURN comunidade, size(membros) AS tamanho, membros ORDER BY tamanho""")

    def documentos(self):
        return self.consultar("""MATCH (d:Documento) OPTIONAL MATCH (d)-[m:MENCIONA]->(e)
RETURN d.id AS id, d.tipo AS tipo, d.titulo AS titulo, d.texto AS texto,
       collect({tipo: labels(e)[0], id: e.id, texto: m.texto_original, papel: m.papel}) AS mencoes
ORDER BY id""")


# ---------------------------------------------------------------------------
# Motor Offline: o MESMO grafo LPG em memória (NetworkX)
# ---------------------------------------------------------------------------
class MotorOffline(MotorBase):
    nome = "Offline (NetworkX em memória)"
    CYPHER_VETOR = "[offline] similaridade de cosseno com numpy sobre dados/embeddings.json"

    def __init__(self, pasta=PASTA):
        import algoritmos
        ler = lambda n: pd.read_csv(pasta / f"{n}.csv", dtype=str)
        self.cli, self.ct, self.end, self.tel = ler("clientes"), ler("contas"), ler("enderecos"), ler("telefones")
        self.disp, self.cd, self.tx = ler("dispositivos"), ler("cliente_dispositivo"), ler("transacoes")
        self.tx["valor"] = self.tx["valor"].astype(float)
        self.tx["data_hora"] = pd.to_datetime(self.tx["data_hora"])
        self.extr = json.loads((pasta / "extracao.json").read_text(encoding="utf-8"))
        emb = json.loads((pasta / "embeddings.json").read_text())
        self.doc_ids = list(emb)
        self.emb = np.array([emb[i] for i in self.doc_ids])
        alg = algoritmos.calcular(pasta)
        self.titular = dict(zip(self.ct["id"], self.ct["cliente_id"].map(dict(zip(self.cli["id"], self.cli["nome"])))))

        G = nx.MultiDiGraph()
        for r in self.cli.itertuples():
            G.add_node(r.id, tipo="Cliente", rotulo=r.nome, comunidade=alg["comunidade_cliente"][r.id])
            G.add_edge(r.id, r.telefone_id, key="TEM_TELEFONE", tipo="TEM_TELEFONE")
            G.add_edge(r.id, r.endereco_id, key="MORA_EM", tipo="MORA_EM")
        for r in self.tel.itertuples():
            G.add_node(r.id, tipo="Telefone", rotulo=r.numero)
        for r in self.end.itertuples():
            G.add_node(r.id, tipo="Endereco", rotulo=r.logradouro)
        for r in self.disp.itertuples():
            G.add_node(r.id, tipo="Dispositivo", rotulo=r.ip)
        for r in self.cd.itertuples():
            G.add_edge(r.cliente_id, r.dispositivo_id, key="USA_DISPOSITIVO", tipo="USA_DISPOSITIVO")
        for r in self.ct.itertuples():
            G.add_node(r.id, tipo="Conta", rotulo=r.id, pagerank=alg["pagerank_conta"][r.id],
                       comunidade=alg["comunidade_conta"][r.id])
            G.add_edge(r.cliente_id, r.id, key="POSSUI", tipo="POSSUI")
        for t in self.tx.itertuples():
            G.add_edge(t.origem, t.destino, key=t.id, tipo="TRANSFERIU", id=t.id, valor=t.valor,
                       data_hora=str(t.data_hora)[:16], canal=t.canal)
        for d in self.extr["documentos"]:
            G.add_node(d["id"], tipo="Documento", rotulo=d["titulo"], texto=d["texto"], doc_tipo=d["tipo"])
            for m in d["mencoes"]:
                if m["id"]:
                    G.add_edge(d["id"], m["id"], key="MENCIONA", tipo="MENCIONA", papel=m["papel"])
            for rel in d["relacoes"]:
                G.add_edge(rel["origem"], rel["destino"], key=rel["doc"], tipo=rel["tipo"], valor=rel["valor"],
                           doc=rel["doc"])
        self.G = G

    def consultar(self, cypher, **params):
        raise RuntimeError("Consultas Cypher livres só funcionam conectado ao Neo4j.")

    def estatisticas(self):
        return {"clientes": len(self.cli), "contas": len(self.ct), "transacoes": len(self.tx),
                "volume": self.tx["valor"].sum(), "documentos": len(self.doc_ids),
                "relacoes": self.G.number_of_edges()}

    def nomes_clientes(self):
        return sorted(self.cli["nome"].tolist())

    def ids_por_nome(self, nomes):
        return self.cli[self.cli["nome"].isin(nomes)]["id"].tolist()

    def busca_vetorial(self, pergunta, k=4):
        from embeddings import embedding_consulta
        sims = self.emb @ embedding_consulta(pergunta)
        saida = []
        for i in sims.argsort()[::-1][:k]:
            did = self.doc_ids[i]
            n = self.G.nodes[did]
            ents = [d for _, d, e in self.G.out_edges(did, data=True) if e["tipo"] == "MENCIONA"]
            saida.append({"id": did, "titulo": n["rotulo"], "texto": n["texto"], "tipo": n["doc_tipo"],
                          "score": float(sims[i]), "entidades": ents})
        return saida

    def busca_textual(self, termo, k=5):
        t = termo.lower()
        linhas = [{"id": d["id"], "titulo": d["titulo"], "texto": d["texto"], "score": d["texto"].lower().count(t)}
                  for d in self.extr["documentos"] if t in d["texto"].lower()]
        return pd.DataFrame(sorted(linhas, key=lambda x: -x["score"])[:k])

    def _sub(self, arestas):
        g = nx.MultiDiGraph()
        for o, d, k in arestas:
            for n in (o, d):
                g.add_node(n, **self.G.nodes[n])
            g.add_edge(o, d, key=k, **self.G[o][d][k])
        return g

    def expandir(self, ids, saltos=2):
        estruturais = {"POSSUI", "USA_DISPOSITIVO", "TEM_TELEFONE", "MORA_EM"}
        arestas, fronteira, visit = set(), [i for i in ids if i in self.G and self.G.nodes[i]["tipo"] != "Documento"], set()
        for _ in range(saltos):
            prox = []
            for n in fronteira:
                visit.add(n)
                for o, d, k, e in list(self.G.out_edges(n, keys=True, data=True)) + list(self.G.in_edges(n, keys=True, data=True)):
                    viz = d if o == n else o
                    if e["tipo"] in estruturais and self.G.nodes[viz]["tipo"] != "Documento":
                        arestas.add((o, d, k))
                        if viz not in visit:
                            prox.append(viz)
            fronteira = prox
        contas = [i for i in ids if i in self.G and self.G.nodes[i]["tipo"] == "Conta"]
        contas += [d for i in ids if i in self.G and self.G.nodes[i]["tipo"] == "Cliente"
                   for _, d, e in self.G.out_edges(i, data=True) if e["tipo"] == "POSSUI"]
        for c in set(contas):  # vítimas que relataram golpe contra a conta (1 salto, sem expandir a vítima)
            arestas |= {(o, d, k) for o, d, k, e in self.G.in_edges(c, keys=True, data=True)
                        if e["tipo"] == "RELATOU_GOLPE"}
        for c in set(contas):
            tx = [(o, d, k, e) for o, d, k, e in list(self.G.out_edges(c, keys=True, data=True)) +
                  list(self.G.in_edges(c, keys=True, data=True)) if e["tipo"] == "TRANSFERIU"]
            for o, d, k, e in sorted(tx, key=lambda x: -x[3]["valor"])[:25]:
                arestas.add((o, d, k))
                outra = d if o == c else o
                for dono, _, kk, ee in self.G.in_edges(outra, keys=True, data=True):
                    if ee["tipo"] == "POSSUI":
                        arestas.add((dono, outra, kk))
        return self._sub(arestas), MotorNeo4j.CYPHER_EXPANDIR.strip()

    def caminho_mais_curto(self, id_a, id_b):
        U = nx.Graph()
        for o, d, k, e in self.G.edges(keys=True, data=True):
            if e["tipo"] != "MENCIONA":
                U.add_edge(o, d, k=(o, d, k))
        try:
            nos = nx.shortest_path(U, id_a, id_b)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return nx.MultiDiGraph(), "shortestPath (offline)"
        return self._sub([U[a][b]["k"] for a, b in zip(nos, nos[1:])]), "shortestPath (offline, NetworkX)"

    def ranking_pagerank(self, n=10):
        linhas = [{"conta": c, "titular": self.titular[c], "pagerank": round(d["pagerank"], 4),
                   "comunidade": d["comunidade"]} for c, d in self.G.nodes(data=True) if d["tipo"] == "Conta"]
        return pd.DataFrame(linhas).sort_values("pagerank", ascending=False).head(n).reset_index(drop=True)

    def comunidades(self):
        df = pd.DataFrame([{"comunidade": d["comunidade"], "nome": d["rotulo"]} for _, d in self.G.nodes(data=True)
                           if d["tipo"] == "Cliente"])
        g = df.groupby("comunidade")["nome"].agg(list).reset_index(name="membros")
        g["tamanho"] = g["membros"].str.len()
        return g.sort_values("tamanho")[["comunidade", "tamanho", "membros"]].reset_index(drop=True)

    def documentos(self):
        return pd.DataFrame([{"id": d["id"], "tipo": d["tipo"], "titulo": d["titulo"], "texto": d["texto"],
                              "mencoes": [{"tipo": m["tipo"], "id": m["id"], "texto": m["texto"], "papel": m["papel"]}
                                          for m in d["mencoes"]]} for d in self.extr["documentos"]])

    # ---- Regras (mesma lógica das consultas Cypher em conhecimento.py) ----
    def regra(self, codigo):
        return getattr(self, f"_{codigo}")()

    def _R1(self):
        lig = []
        nome = dict(zip(self.cli["id"], self.cli["nome"]))
        for o, d, e in self.G.edges(data=True):
            if e["tipo"] in ("USA_DISPOSITIVO", "TEM_TELEFONE", "MORA_EM"):
                lig.append({"tipo": self.G.nodes[d]["tipo"], "identificador": self.G.nodes[d]["rotulo"], "x": d,
                            "nome": nome[o]})
        df = pd.DataFrame(lig)
        g = df.groupby(["tipo", "identificador", "x"])["nome"].agg(lambda s: sorted(set(s))).reset_index(name="clientes")
        g["qtd_clientes"] = g["clientes"].str.len()
        g = g[g["qtd_clientes"] >= 3].sort_values("qtd_clientes", ascending=False)
        return g[["tipo", "identificador", "qtd_clientes", "clientes"]].reset_index(drop=True)

    def _R2(self):
        grandes = self.tx[self.tx["valor"] >= 20000]
        g = nx.MultiDiGraph()
        for t in grandes.itertuples():
            g.add_edge(t.origem, t.destino, valor=t.valor, dh=t.data_hora)
        linhas = []
        for ciclo in nx.simple_cycles(nx.DiGraph(g), length_bound=5):
            if len(ciclo) < 3:
                continue
            i = ciclo.index(min(ciclo))
            ciclo = ciclo[i:] + ciclo[:i]
            ts, ultimo = [], None
            for o, d in zip(ciclo, ciclo[1:] + ciclo[:1]):
                cand = sorted((e for e in g[o][d].values() if ultimo is None or e["dh"] > ultimo), key=lambda e: e["dh"])
                if not cand:
                    break
                ts.append(cand[0])
                ultimo = cand[0]["dh"]
            if len(ts) != len(ciclo):
                continue
            horas = int((ts[-1]["dh"] - ts[0]["dh"]).total_seconds() // 3600)
            if horas <= 72:
                linhas.append({"contas": ciclo + [ciclo[0]], "saltos": len(ts), "valor_inicial": round(ts[0]["valor"], 2),
                               "valor_final": round(ts[-1]["valor"], 2), "horas": horas,
                               "clientes": sorted({self.titular[c] for c in ciclo})})
        return pd.DataFrame(linhas, columns=["contas", "saltos", "valor_inicial", "valor_final", "horas", "clientes"])

    def _R3(self):
        novas = set(self.ct[self.ct["data_abertura"] >= "2026-06-01"]["id"])
        ent = self.tx[self.tx["destino"].isin(novas)].groupby("destino").agg(
            origens=("origem", "nunique"), recebido=("valor", "sum"))
        linhas = []
        for conta, r in ent[ent["origens"] >= 8].iterrows():
            saida = self.tx[self.tx["origem"] == conta]
            if saida["valor"].sum() >= 0.8 * r["recebido"]:
                linhas.append({"conta": conta, "titular": self.titular[conta], "origens": int(r["origens"]),
                               "recebido": round(r["recebido"], 2), "repassado": round(saida["valor"].sum(), 2),
                               "percentual_repassado": round(100 * saida["valor"].sum() / r["recebido"], 1),
                               "destinos": sorted(saida["destino"].unique()), "clientes": [self.titular[conta]]})
        return pd.DataFrame(linhas, columns=["conta", "titular", "origens", "recebido", "repassado",
                                             "percentual_repassado", "destinos", "clientes"])

    def _R4(self):
        f = self.tx[(self.tx["valor"] >= LIMITE_ALERTA * 0.9) & (self.tx["valor"] < LIMITE_ALERTA)]
        g = f.groupby(["origem", "destino"]).agg(qtd=("id", "count"), total=("valor", "sum")).reset_index()
        return pd.DataFrame([{"origem": r.origem, "titular_origem": self.titular[r.origem], "destino": r.destino,
                              "titular_destino": self.titular[r.destino], "qtd": r.qtd, "total": round(r.total, 2),
                              "clientes": [self.titular[r.origem], self.titular[r.destino]]}
                             for r in g[g["qtd"] >= 4].itertuples()],
                            columns=["origem", "titular_origem", "destino", "titular_destino", "qtd", "total", "clientes"])


# ---------------------------------------------------------------------------
# PILAR 4 - Visualização do subgrafo (PyVis / vis.js)
# ---------------------------------------------------------------------------
CORES = {"Cliente": "#4C8BF5", "Conta": "#F5A623", "Dispositivo": "#D0021B", "Telefone": "#9013FE",
         "Endereco": "#417505", "Documento": "#7F8C8D"}


def grafo_html(g: nx.MultiDiGraph, destaque=(), altura="560px") -> str:
    from pyvis.network import Network
    net = Network(height=altura, width="100%", directed=True, bgcolor="#ffffff", cdn_resources="in_line")
    for n, d in g.nodes(data=True):
        grande = d["tipo"] in ("Cliente", "Conta")
        extra = f"\nPageRank: {d['pagerank']:.4f}" if "pagerank" in d else ""
        extra += f"\nComunidade: {d['comunidade']}" if "comunidade" in d else ""
        net.add_node(n, label=str(d.get("rotulo", n))[:26], color=CORES.get(d["tipo"], "#999"),
                     title=f"{d['tipo']} [{n}]: {d.get('rotulo')}{extra}", size=26 if n in destaque else 20 if grande else 12,
                     borderWidth=4 if n in destaque else 1)
    for o, dst, e in g.edges(data=True):
        v = e.get("valor") or 0
        rot = f"R$ {v:,.0f}".replace(",", ".") if e.get("tipo") == "TRANSFERIU" else e.get("tipo", "")
        net.add_edge(o, dst, title=f"{e.get('tipo')} {rot}", label=rot, width=1 + min(v / 10000, 5),
                     color="#E94B3C" if e.get("tipo") in ("RELATOU_GOLPE",) or v >= 9000 else "#999")
    net.set_options('{"physics": {"barnesHut": {"springLength": 150}}, "edges": {"font": {"size": 9}, '
                    '"smooth": {"type": "curvedCW", "roundness": 0.12}}}')
    return net.generate_html()
