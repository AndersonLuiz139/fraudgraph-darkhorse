"""
Integração com LLM (qualquer provedor compatível com a API da OpenAI):
  - Google Gemini (tem plano gratuito)
  - OpenAI
  - Ollama (roda local, sem internet)

Configure no arquivo .env (veja .env.exemplo).
"""
import json
import os
import re

from dotenv import load_dotenv

from conhecimento import ESQUEMA, REGRAS, texto_regras

load_dotenv()


def llm_configurado() -> bool:
    return bool(os.getenv("LLM_MODEL")) and bool(os.getenv("LLM_API_KEY") or "localhost" in os.getenv("LLM_BASE_URL", ""))


def _cliente():
    from openai import OpenAI
    return OpenAI(base_url=os.getenv("LLM_BASE_URL") or None, api_key=os.getenv("LLM_API_KEY") or "ollama")


def _chat(sistema: str, usuario: str, temperatura=0.1) -> str:
    r = _cliente().chat.completions.create(
        model=os.getenv("LLM_MODEL"), temperature=temperatura,
        messages=[{"role": "system", "content": sistema}, {"role": "user", "content": usuario}])
    return r.choices[0].message.content


# ---------------------------------------------------------------------------
# KAG - passo 1: transformar a pergunta em um PLANO LÓGICO (logical form)
# ---------------------------------------------------------------------------
def planejar(pergunta: str, permite_cypher: bool) -> dict:
    sistema = f"""Você é um analista de prevenção a fraudes que consulta um GRAFO DE CONHECIMENTO.

ESQUEMA DO GRAFO (Neo4j):
{ESQUEMA}

REGRAS DE ESPECIALISTA DISPONÍVEIS:
{texto_regras()}

Sua tarefa: decompor a pergunta do usuário em um plano lógico de passos e escolher como buscar a resposta.
Responda SOMENTE com um JSON válido, sem texto antes ou depois, neste formato:
{{"plano": ["passo 1 ...", "passo 2 ..."],
  "regras": ["R1", ...],
  "cypher": "{'consulta Cypher de LEITURA se nenhuma regra resolver a pergunta, senão string vazia' if permite_cypher else ''}"}}
- Use "regras" sempre que alguma regra se aplicar. Para perguntas sobre quadrilha/ligação entre alertas use todas.
- {'Se precisar de Cypher, use só MATCH/WHERE/RETURN/WITH, nomes exatos do esquema, e limite com LIMIT 50.' if permite_cypher else 'NÃO gere Cypher (campo cypher deve ser string vazia).'}
"""
    bruto = _chat(sistema, pergunta)
    m = re.search(r"\{.*\}", bruto, re.S)
    try:
        plano = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        plano = {}
    plano.setdefault("plano", [])
    plano["regras"] = [r for r in plano.get("regras", []) if r in REGRAS]
    plano["cypher"] = (plano.get("cypher") or "").strip() if permite_cypher else ""
    plano["bruto"] = bruto
    return plano


# ---------------------------------------------------------------------------
# KAG - passo 3: gerar a resposta FUNDAMENTADA nos fatos do grafo
# ---------------------------------------------------------------------------
def responder_kag(pergunta: str, fatos: str) -> str:
    sistema = """Você é um analista de prevenção a fraudes. Responda em português do Brasil,
de forma clara e objetiva, usando APENAS os FATOS fornecidos (resultados do grafo).
REGRA DE OURO (grounding): toda afirmação deve citar sua procedência entre colchetes:
  - o CAMINHO do grafo usado, ex.: [Cliente:Dom Vasconcelos -POSSUI-> Conta:CT-0162]
  - ou o documento, ex.: [DOC-004]
  - ou a regra de especialista, ex.: [R3]
Nunca invente nomes, contas ou valores que não estejam nos fatos.
Se os fatos não forem suficientes, diga isso. Termine com uma recomendação curta de ação
(ex.: bloquear cautelarmente, comunicar ao COAF, analisar manualmente)."""
    return _chat(sistema, f"PERGUNTA: {pergunta}\n\nFATOS DO GRAFO:\n{fatos}", 0.2)


# ---------------------------------------------------------------------------
# RAG tradicional: resposta com trechos de texto recuperados por similaridade
# ---------------------------------------------------------------------------
def responder_rag(pergunta: str, trechos: list) -> str:
    sistema = """Você é um analista de prevenção a fraudes. Responda em português do Brasil
usando APENAS os trechos de documentos fornecidos. Se não for possível concluir, diga isso."""
    contexto = "\n".join(f"- {t}" for t in trechos)
    return _chat(sistema, f"PERGUNTA: {pergunta}\n\nTRECHOS RECUPERADOS:\n{contexto}", 0.2)
