"""
Testes operacionais do pipeline (rodam sem Neo4j e sem LLM).

    pytest -q
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from conhecimento import REGRAS  # noqa: E402
from extracao import (ExtratorLLM, ExtratorRegras, ResolvedorEntidades, normalizar_telefone,  # noqa: E402
                      processar_documento)
from motor import MotorOffline, cypher_somente_leitura  # noqa: E402

GABARITO = json.loads((RAIZ / "dados" / "gabarito.json").read_text(encoding="utf-8"))
QA, GB = GABARITO["quadrilha_A"], GABARITO["grupo_B"]


@pytest.fixture(scope="module")
def motor():
    return MotorOffline()


@pytest.fixture(scope="module")
def resolvedor():
    return ResolvedorEntidades()


# ---------- Pilar 1: Text-to-Graph ----------
def test_normaliza_telefone_em_formatos_diferentes():
    formatos = ["(77) 98232-9832", "77982329832", "+55 77 98232 9832", "77 98232-9832"]
    assert len({normalizar_telefone(f) for f in formatos}) == 1


def test_resolucao_de_nomes_abreviados_e_maiusculos(resolvedor):
    mula = QA["clientes"][1]
    partes = mula.split()
    variantes = [mula, mula.upper(), f"{partes[0][0]}. {' '.join(partes[1:])}"]
    ids = {resolvedor.pessoa(v)[0] for v in variantes}
    assert len(ids) == 1 and None not in ids


def test_extracao_por_regras_cria_relacao_de_golpe(resolvedor):
    mula_conta = QA["conta_mula"]
    doc = {"id": "T1", "tipo": "Contestação Pix (MED)", "autor_cliente_id": "CLI-0001", "data": "2026-10-10",
           "titulo": "t", "texto": f"Fiz um Pix de R$ 1.234,56 para a conta {mula_conta[-4:]} de {QA['clientes'][1]}."}
    r = processar_documento(doc, ExtratorRegras(), resolvedor)
    assert r["valores"] == [1234.56]
    assert r["relacoes"][0]["destino"] == mula_conta


def test_extrator_llm_usa_o_mesmo_resolvedor(resolvedor):
    resposta = json.dumps({"pessoas": [QA["clientes"][1].upper()], "contas": ["CT-" + QA["conta_mula"][-4:]],
                           "telefones": [], "valores": ["R$ 10,00"]})
    with patch("llm._chat", return_value=resposta):
        ents = ExtratorLLM().entidades("qualquer texto")
    assert ents["contas"] == [QA["conta_mula"][-4:]]
    assert resolvedor.pessoa(ents["pessoas"][0])[0] is not None


def test_todas_as_mencoes_foram_resolvidas():
    est = json.loads((RAIZ / "dados" / "extracao.json").read_text(encoding="utf-8"))["estatisticas"]
    assert est["nao_resolvidas"] == 0
    assert est["grafias_diferentes_de_pessoas"] > est["pessoas_canonicas"]


# ---------- Pilar 3: regras KAG, multi-hop e algoritmos ----------
def test_R1_identifica_identidades_compartilhadas(motor):
    nomes = {n for lst in motor.regra("R1")["clientes"] for n in lst}
    assert nomes == (set(QA["clientes"]) - {QA["clientes"][5]}) | set(GB["clientes"])


def test_R2_encontra_os_dois_ciclos(motor):
    ciclos = {" -> ".join(c) for c in motor.regra("R2")["contas"]}
    assert ciclos == {QA["ciclo_lavagem"], GB["ciclo_lavagem"]}


def test_R3_encontra_somente_a_conta_mula(motor):
    df = motor.regra("R3")
    assert df["conta"].tolist() == [QA["conta_mula"]]  # lojas recebem de muitos, mas não entram


def test_R4_encontra_o_fracionamento(motor):
    df = motor.regra("R4")
    assert len(df) == 1 and df.iloc[0]["qtd"] == 6


def test_multi_hop_une_alertas_em_uma_quadrilha(motor):
    grupos = motor.agrupar_quadrilhas(motor.todas_regras())
    assert sorted(grupos[0]["clientes"]) == sorted(QA["clientes"])
    assert grupos[0]["regras_disparadas"] == ["R1", "R2", "R3", "R4"]


def test_louvain_isola_a_quadrilha(motor):
    com = motor.comunidades()
    conjuntos = [set(m) for m in com["membros"]]
    assert set(QA["clientes"]) in conjuntos and set(GB["clientes"]) in conjuntos


def test_caminho_mais_curto_de_vitima_ate_quadrilha(motor):
    vitima = next(o for o, d, e in motor.G.edges(data=True) if e["tipo"] == "RELATOU_GOLPE")
    g, _ = motor.caminho_mais_curto(vitima, motor.ids_por_nome([QA["clientes"][5]])[0])
    assert 1 <= g.number_of_edges() <= 4


def test_recuperacao_hibrida_traz_documentos_e_subgrafo(motor):
    ctx = motor.recuperar_contexto("golpe da falsa central, fiz um Pix", ["R3"])
    assert ctx["documentos"] and ctx["subgrafo"].number_of_edges() > 0
    assert QA["conta_mula"] in ctx["sementes"]
    assert motor.confianca(ctx)["nivel"] in {"Alta", "Média"}


# ---------- Segurança ----------
@pytest.mark.parametrize("q", ["MATCH (n) DETACH DELETE n", "CREATE (n:X)", "MATCH (n) SET n.x = 1"])
def test_bloqueia_cypher_de_escrita(q):
    assert not cypher_somente_leitura(q)


def test_regras_sao_somente_leitura():
    assert all(cypher_somente_leitura(r["cypher"]) for r in REGRAS.values())
