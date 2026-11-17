"""
PILAR 4 - Interface e Visualização (Streamlit)

Rodar:  streamlit run app.py
"""
import json
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from conhecimento import ESQUEMA, PERGUNTAS_PRONTAS, REGRAS
from motor import MotorBase, MotorNeo4j, MotorOffline, cypher_somente_leitura, grafo_html
import llm

load_dotenv()
PASTA = Path(__file__).parent / "dados"
st.set_page_config(page_title="FraudGraph · GraphRAG + KAG", page_icon="🕸️", layout="wide")


# ---------------------------------------------------------------------------
# Conexão (Neo4j se disponível; senão modo offline)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Conectando ao grafo...")
def obter_motor(preferir_neo4j: bool):
    if preferir_neo4j:
        try:
            return MotorNeo4j(os.getenv("NEO4J_URI", "bolt://localhost:7687"), os.getenv("NEO4J_USER", "neo4j"),
                              os.getenv("NEO4J_PASSWORD", "senha12345")), None
        except Exception as e:
            return MotorOffline(), f"Não conectou ao Neo4j ({type(e).__name__}). Usando modo offline."
    return MotorOffline(), None


@st.cache_resource
def obter_rag():
    from rag_texto import BuscadorTexto
    return BuscadorTexto()


with st.sidebar:
    st.title("🕸️ FraudGraph")
    st.caption("Detecção de fraudes financeiras com GraphRAG + KAG")
    preferir = st.toggle("Usar Neo4j", value=True, help="Desligue para o modo offline (plano B, sem banco).")
    motor, aviso = obter_motor(preferir)
    if aviso:
        st.warning(aviso)
    st.success(f"Motor: **{motor.nome}**")
    if llm.llm_configurado():
        st.success(f"LLM: **{os.getenv('LLM_MODEL')}**")
    else:
        st.info("LLM não configurado: respostas por modelo (template). Configure o .env para usar o LLM.")
    st.markdown("**Legenda**")
    st.markdown("🔵 Cliente &nbsp; 🟠 Conta &nbsp; 🔴 Dispositivo<br>🟣 Telefone &nbsp; 🟢 Endereço &nbsp; "
                "<span style='color:#E94B3C'>━</span> valor alto / golpe", unsafe_allow_html=True)


def mostrar_grafo(g, destaque=(), altura=560):
    if g.number_of_nodes() == 0:
        st.info("Nenhum nó para exibir.")
        return
    html = grafo_html(g, destaque, f"{altura}px")
    if hasattr(st, "iframe"):  # Streamlit >= 1.52
        st.iframe(html, height=altura + 20)
    else:
        components.html(html, height=altura + 20, scrolling=False)


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


aba = st.tabs(["🏠 Visão geral", "📄 Text-to-Graph", "🚨 Regras (KAG)", "💬 Perguntar (RAG × KAG)", "🔎 Investigar"])

# ---------------------------------------------------------------------------
# 1. VISÃO GERAL
# ---------------------------------------------------------------------------
with aba[0]:
    est = motor.estatisticas()
    c = st.columns(6)
    c[0].metric("Clientes", int(est["clientes"]))
    c[1].metric("Contas", int(est["contas"]))
    c[2].metric("Transações", int(est["transacoes"]))
    c[3].metric("Volume", brl(float(est["volume"])))
    c[4].metric("Documentos", int(est["documentos"]))
    c[5].metric("Relações", int(est["relacoes"]))

    col1, col2 = st.columns([1.1, 1])
    with col1:
        st.subheader("Modelo LPG (Labeled Property Graph)")
        st.graphviz_chart("""digraph {
          rankdir=LR; node [shape=box, style="rounded,filled", fontname=Helvetica, fontsize=11];
          Cliente [fillcolor="#cfe0ff"]; Conta [fillcolor="#ffe3b3"]; Dispositivo [fillcolor="#ffc9c9"];
          Telefone [fillcolor="#e5ccff"]; Endereco [fillcolor="#d6f0c2"]; Documento [fillcolor="#e6e6e6"];
          Cliente -> Conta [label="POSSUI"]; Cliente -> Dispositivo [label="USA_DISPOSITIVO"];
          Cliente -> Telefone [label="TEM_TELEFONE"]; Cliente -> Endereco [label="MORA_EM"];
          Conta -> Conta [label="TRANSFERIU\\n{valor, data_hora, canal}"];
          Cliente -> Conta [label="RELATOU_GOLPE\\n(extraído do texto)", color=red, fontcolor=red];
          Documento -> Cliente [label="MENCIONA", style=dashed]; Documento -> Conta [label="MENCIONA", style=dashed];
          Documento -> Telefone [label="MENCIONA", style=dashed];
        }""")
    with col2:
        st.subheader("Pipeline")
        st.graphviz_chart("""digraph { rankdir=TB; node [shape=box, style="rounded,filled", fillcolor="#f2f2f2", fontname=Helvetica, fontsize=11];
          a [label="1. Text-to-Graph\\nNER + RE + Resolução de Entidades"]; b [label="2. Neo4j LPG\\nconstraints + índices B-Tree/Fulltext/Vetor"];
          c [label="3. Recuperação híbrida\\nvetor + Cypher multi-hop + PageRank/Louvain + regras"]; d [label="4. Interface\\nsubgrafo + Cypher + confiança"];
          a -> b -> c -> d }""")

    col3, col4 = st.columns(2)
    with col3:
        st.subheader("PageRank (para onde o dinheiro converge)")
        st.dataframe(motor.ranking_pagerank(10), hide_index=True)
    with col4:
        st.subheader("Comunidades (Louvain) — menores primeiro")
        com = motor.comunidades()
        st.dataframe(com.head(6), hide_index=True)
        st.caption("Comunidades pequenas e muito coesas costumam merecer atenção.")

# ---------------------------------------------------------------------------
# 2. TEXT-TO-GRAPH
# ---------------------------------------------------------------------------
with aba[1]:
    st.subheader("Do texto livre para o grafo: NER → RE → Resolução de Entidades")
    extr = json.loads((PASTA / "extracao.json").read_text(encoding="utf-8"))
    e = extr["estatisticas"]
    c = st.columns(5)
    c[0].metric("Documentos", e["documentos"])
    c[1].metric("Menções extraídas", e["mencoes"])
    c[2].metric("Resolvidas", f"{e['resolvidas']}/{e['mencoes']}")
    c[3].metric("Grafias de pessoas → nós", f"{e['grafias_diferentes_de_pessoas']} → {e['pessoas_canonicas']}")
    c[4].metric("Relações RELATOU_GOLPE", e["relacoes_extraidas"])
    st.caption(f"Extrator usado: {e['extrator']}")

    docs = extr["documentos"]
    escolha = st.selectbox("Documento", [f"{d['id']} · {d['tipo']} · {d['titulo']}" for d in docs])
    d = docs[[f"{x['id']} · {x['tipo']} · {x['titulo']}" for x in docs].index(escolha)]
    texto = d["texto"]
    for m in sorted(d["mencoes"], key=lambda m: -len(m["texto"])):
        cor = {"Cliente": "#cfe0ff", "Conta": "#ffe3b3", "Telefone": "#e5ccff"}[m["tipo"]]
        texto = texto.replace(m["texto"], f"<mark style='background:{cor}'>{m['texto']}</mark>")
    st.markdown(f"<div style='padding:12px;border:1px solid #ddd;border-radius:8px'>{texto}</div>", unsafe_allow_html=True)
    st.markdown("**Menções → nó canônico (Resolução de Entidades)**")
    st.dataframe(pd.DataFrame(d["mencoes"]), hide_index=True)
    if d["relacoes"]:
        st.markdown("**Relações extraídas**")
        st.dataframe(pd.DataFrame(d["relacoes"]), hide_index=True)

    st.divider()
    st.markdown("**Teste ao vivo:** cole um relato e veja a extração")
    exemplo = ("Recebi mensagem do número +55 77 98232 9832 dizendo ser do banco. Fiz um Pix de R$ 1.500,00 "
               "para a conta 0162 de DOM VASCONCELOS.")
    livre = st.text_area("Texto", exemplo, height=90)
    usar_llm = st.checkbox("Extrair com LLM", value=False, disabled=not llm.llm_configurado())
    if st.button("Extrair entidades"):
        from extracao import ExtratorLLM, ExtratorRegras, ResolvedorEntidades, processar_documento
        ext = ExtratorLLM() if usar_llm else ExtratorRegras()
        r = processar_documento({"id": "TESTE", "tipo": "Contestação Pix (MED)", "autor_cliente_id": "",
                                 "data": "2026-11-18", "titulo": "teste", "texto": livre}, ext, ResolvedorEntidades())
        st.dataframe(pd.DataFrame(r["mencoes"]), hide_index=True)
        st.caption(f"Valores encontrados: {[brl(v) for v in r['valores']]}")

# ---------------------------------------------------------------------------
# 3. REGRAS (KAG)
# ---------------------------------------------------------------------------
with aba[2]:
    st.subheader("Conhecimento de especialista transformado em regras sobre o grafo")
    resultados = motor.todas_regras()
    for cod, regra in REGRAS.items():
        df = resultados[cod]
        with st.expander(f"**{cod} · {regra['nome']}** — {len(df)} achado(s)", expanded=(cod == "R1")):
            st.write(regra["descricao"])
            st.code(regra["cypher"].strip(), language="cypher")
            st.dataframe(df, hide_index=True)
            if not df.empty and st.button(f"Ver subgrafo de {cod}", key=f"v{cod}"):
                nomes = sorted({n for lst in df["clientes"] for n in lst})
                ids = motor.ids_por_nome(nomes)
                g, _ = motor.expandir(ids, 1)
                mostrar_grafo(g, destaque=ids)
    st.subheader("Raciocínio multi-hop: os alertas estão ligados?")
    for grp in motor.agrupar_quadrilhas(resultados):
        st.markdown(f"- **{grp['qtd_clientes']} clientes** · regras {', '.join(grp['regras_disparadas'])}: "
                    f"{', '.join(grp['clientes'])}")

# ---------------------------------------------------------------------------
# 4. PERGUNTAR (RAG × GraphRAG/KAG)
# ---------------------------------------------------------------------------
PALAVRAS_REGRAS = {"R1": r"compartilh|telefone|endere|celular|dispositivo|anel|identidade",
                   "R2": r"ciclo|lavagem|circul|volta",
                   "R3": r"mula|golpe|repass|v[ií]tima|dinheiro|para onde|falsa central",
                   "R4": r"fracion|limite|smurf|abaixo|divid"}


def planejar_sem_llm(p):
    if re.search(r"quadrilha|ligad|rede|conect|todos|grupo|esquema", p, re.I):
        regras = list(REGRAS)
    else:
        regras = [r for r, pad in PALAVRAS_REGRAS.items() if re.search(pad, p, re.I)]
    return {"plano": ["Buscar documentos semelhantes à pergunta (índice vetorial)",
                      "Identificar entidades citadas nos documentos e na pergunta (sementes)",
                      "Percorrer o grafo a partir das sementes (2 saltos + transferências)",
                      f"Aplicar regras de especialista: {', '.join(regras) or 'nenhuma específica'}",
                      "Consolidar os fatos e responder citando a procedência"],
            "regras": regras, "cypher": ""}


def fatos_texto(ctx, extra_cypher=None):
    partes = ["DOCUMENTOS (busca vetorial):"]
    partes += [f"[{d['id']}] (similaridade {d['score']:.2f}) {d['texto']}" for d in ctx["documentos"]]
    for cod, df in ctx["regras"].items():
        partes.append(f"\nREGRA {cod} - {REGRAS[cod]['nome']}: {len(df)} achado(s)")
        partes += [str(r) for r in df.drop(columns=[], errors="ignore").to_dict("records")[:10]]
    if ctx["quadrilhas"]:
        partes.append("\nGRUPOS LIGADOS (união dos alertas):")
        partes += [str(q) for q in ctx["quadrilhas"]]
    g = ctx["subgrafo"]
    algo = [f"{n}: comunidade={d.get('comunidade')}, pagerank={d.get('pagerank', 0):.4f}"
            for n, d in g.nodes(data=True) if n in ctx["sementes"] and "comunidade" in d]
    if algo:
        partes.append("\nALGORITMOS DE GRAFO (sementes):")
        partes += algo
    partes.append("\nCAMINHOS DO SUBGRAFO:")
    partes += MotorBase.caminhos_texto(g, 80)
    if extra_cypher is not None and not extra_cypher.empty:
        partes.append("\nRESULTADO DO CYPHER GERADO:")
        partes.append(extra_cypher.head(30).to_string())
    return "\n".join(partes)


def resposta_sem_llm(ctx):
    linhas = []
    if ctx["documentos"]:
        linhas.append("**Documentos mais relevantes:** " + ", ".join(
            f"{d['id']} ({d['score']:.2f})" for d in ctx["documentos"]))
    for cod, df in ctx["regras"].items():
        if df.empty:
            linhas.append(f"- **{cod}** ({REGRAS[cod]['nome']}): nenhum achado.")
            continue
        if cod == "R1":
            for r in df.itertuples():
                linhas.append(f"- **R1**: {r.qtd_clientes} clientes compartilham o mesmo {r.tipo.lower()} "
                              f"({r.identificador}): {', '.join(r.clientes)}.")
        elif cod == "R2":
            for r in df.itertuples():
                linhas.append(f"- **R2**: ciclo {' → '.join(r.contas)} em {r.horas}h, de {brl(r.valor_inicial)} "
                              f"para {brl(r.valor_final)} ({', '.join(r.clientes)}).")
        elif cod == "R3":
            for r in df.itertuples():
                linhas.append(f"- **R3**: conta {r.conta} ({r.titular}) recebeu de {r.origens} contas "
                              f"({brl(r.recebido)}) e repassou {r.percentual_repassado}% para {', '.join(r.destinos)}.")
        elif cod == "R4":
            for r in df.itertuples():
                linhas.append(f"- **R4**: {r.qtd} transferências logo abaixo do limite de {r.titular_origem} "
                              f"({r.origem}) para {r.titular_destino} ({r.destino}), total {brl(r.total)}.")
    for q in ctx["quadrilhas"]:
        if len(q["regras_disparadas"]) > 1:
            linhas.append(f"\n**Conclusão multi-hop:** os alertas {', '.join(q['regras_disparadas'])} ligam "
                          f"{q['qtd_clientes']} clientes em uma mesma rede: {', '.join(q['clientes'])}.")
    sementes_cli = [(n, d) for n, d in ctx["subgrafo"].nodes(data=True) if n in ctx["sementes"] and d["tipo"] == "Cliente"]
    for n, d in sementes_cli[:5]:
        linhas.append(f"- {d['rotulo']} ({n}) pertence à comunidade {d.get('comunidade')} (Louvain).")
    g = ctx["subgrafo"]
    contas_semente = [n for n in ctx["sementes"] if n in g and g.nodes[n]["tipo"] == "Conta"]
    for c in contas_semente[:3]:
        saidas = [(d, e["valor"]) for _, d, e in g.out_edges(c, data=True) if e.get("tipo") == "TRANSFERIU"]
        if saidas:
            destinos = {}
            for d, v in saidas:
                destinos[d] = destinos.get(d, 0) + v
            dono = lambda x: next((g.nodes[o]["rotulo"] for o, _, e in g.in_edges(x, data=True)
                                   if e.get("tipo") == "POSSUI"), "?")
            linhas.append(f"- **Caminho do dinheiro:** a conta {c} ({dono(c)}) enviou " + "; ".join(
                f"{brl(v)} para {d} ({dono(d)})" for d, v in sorted(destinos.items(), key=lambda x: -x[1])) + ".")
    golpes = [e for _, _, e in g.edges(data=True) if e.get("tipo") == "RELATOU_GOLPE"]
    if golpes:
        linhas.append(f"- {len(golpes)} relatos de golpe (extraídos do texto) apontam para contas deste subgrafo, "
                      f"somando {brl(sum(e.get('valor') or 0 for e in golpes))}.")
    return "\n".join(linhas) or "Não encontrei fatos suficientes no grafo para responder."


with aba[3]:
    st.subheader("Pergunte ao sistema")
    exemplos = list(PERGUNTAS_PRONTAS) + ["Quem é Dom Vasconcelos e com quem ele se relaciona?",
                                          "Recebi ligação de uma falsa central e fiz um Pix. Para onde foi o dinheiro?"]
    ex = st.selectbox("Exemplos", ["(escreva a sua)"] + exemplos, key="exemplo")
    pergunta = st.text_input("Pergunta", "" if ex == "(escreva a sua)" else ex, key=f"pergunta_{ex}")
    comparar = st.checkbox("Comparar com RAG tradicional (só texto)", value=True)

    if st.button("Responder", type="primary") and pergunta:
        col_kag, col_rag = st.columns(2) if comparar else (st.container(), None)

        with col_kag:
            st.markdown("### 🕸️ GraphRAG + KAG")
            with st.spinner("Planejando..."):
                usa_cypher = isinstance(motor, MotorNeo4j)
                try:
                    plano = llm.planejar(pergunta, usa_cypher) if llm.llm_configurado() else planejar_sem_llm(pergunta)
                except Exception as e:
                    st.warning(f"LLM falhou no planejamento ({e}); usando planejador por regras.")
                    plano = planejar_sem_llm(pergunta)
            st.markdown("**1. Plano lógico (logical form)**")
            st.markdown("\n".join(f"{i + 1}. {p}" for i, p in enumerate(plano["plano"])))

            with st.spinner("Recuperando contexto no grafo..."):
                ctx = motor.recuperar_contexto(pergunta, plano["regras"])
                extra = None
                if plano.get("cypher"):
                    if cypher_somente_leitura(plano["cypher"]):
                        try:
                            extra = motor.consultar(plano["cypher"])
                            ctx["cypher"].append(plano["cypher"])
                        except Exception as e:
                            st.warning(f"O Cypher gerado pelo LLM falhou: {e}")
                    else:
                        st.error("Cypher gerado bloqueado (não é somente leitura).")

            st.markdown("**2. Documentos recuperados (busca vetorial)**")
            st.dataframe(pd.DataFrame(ctx["documentos"])[["id", "titulo", "score"]], hide_index=True)
            st.markdown("**3. Subgrafo de contexto**")
            mostrar_grafo(ctx["subgrafo"], destaque=ctx["sementes"], altura=430)

            with st.spinner("Gerando resposta..."):
                if llm.llm_configurado():
                    try:
                        resposta = llm.responder_kag(pergunta, fatos_texto(ctx, extra))
                    except Exception as e:
                        resposta = f"(LLM indisponível: {e})\n\n" + resposta_sem_llm(ctx)
                else:
                    resposta = resposta_sem_llm(ctx)
            st.markdown("**4. Resposta**")
            st.markdown(resposta)

            conf = motor.confianca(ctx)
            cor = {"Alta": "green", "Média": "orange", "Baixa": "red"}[conf["nivel"]]
            st.markdown(f"**Confiança:** :{cor}[{conf['nivel']}] ({conf['pontuacao']}) — {conf['detalhe']}")
            with st.expander("🔍 Rastreabilidade (provenance)"):
                st.markdown("**Nós semente:** " + ", ".join(ctx["sementes"]))
                st.markdown(f"**Nós consultados ({ctx['subgrafo'].number_of_nodes()}):**")
                st.dataframe(pd.DataFrame([{"id": n, "tipo": d["tipo"], "rotulo": d["rotulo"]}
                                           for n, d in ctx["subgrafo"].nodes(data=True)]), hide_index=True, height=200)
                st.markdown("**Consultas Cypher executadas:**")
                for q in ctx["cypher"]:
                    st.code(q, language="cypher")
                if extra is not None:
                    st.dataframe(extra)
                st.markdown("**Caminhos usados como contexto:**")
                st.code("\n".join(MotorBase.caminhos_texto(ctx["subgrafo"], 60)), language="text")

        if col_rag is not None:
            with col_rag:
                st.markdown("### 📄 RAG tradicional")
                st.caption("Busca só por semelhança de texto em extratos e cadastros, sem enxergar ligações.")
                trechos = obter_rag().buscar(pergunta, 8)
                st.markdown("**Trechos recuperados**")
                for t in trechos:
                    st.markdown(f"- {t}")
                if llm.llm_configurado():
                    try:
                        st.markdown("**Resposta**")
                        st.markdown(llm.responder_rag(pergunta, trechos))
                    except Exception as e:
                        st.warning(f"LLM indisponível: {e}")
                else:
                    st.info("Sem LLM, o RAG apenas lista os trechos. Repare que eles não revelam a rede de fraude.")

# ---------------------------------------------------------------------------
# 5. INVESTIGAR
# ---------------------------------------------------------------------------
with aba[4]:
    nomes = motor.nomes_clientes()
    st.subheader("Vizinhança de um cliente (2 saltos)")
    padrao = nomes.index("Dom Vasconcelos") if "Dom Vasconcelos" in nomes else 0
    alvo = st.selectbox("Cliente", nomes, index=padrao)
    ids = motor.ids_por_nome([alvo])
    g, cy = motor.expandir(ids, 2)
    mostrar_grafo(g, destaque=ids)
    with st.expander("Cypher usado"):
        st.code(cy, language="cypher")

    st.subheader("Caminho mais curto entre dois clientes")
    c1, c2 = st.columns(2)
    a = c1.selectbox("De", nomes, index=0, key="ca")
    b = c2.selectbox("Para", nomes, index=padrao, key="cb")
    if st.button("Encontrar caminho"):
        ia, ib = motor.ids_por_nome([a])[0], motor.ids_por_nome([b])[0]
        g, cy = motor.caminho_mais_curto(ia, ib)
        if g.number_of_edges():
            st.success(f"Caminho com {g.number_of_edges()} saltos.")
            mostrar_grafo(g, destaque=[ia, ib], altura=380)
            st.code("\n".join(MotorBase.caminhos_texto(g)), language="text")
        else:
            st.info("Não há caminho entre esses clientes.")
        st.code(cy, language="cypher")
