"""
Embeddings dos documentos (para o índice VETORIAL do Neo4j).

- Se EMBED_MODEL estiver no .env, usa o provedor configurado (Gemini, OpenAI ou Ollama).
- Senão, usa LSA local (TF-IDF + SVD): técnica clássica de embedding, roda sem internet.

Uso:
    python embeddings.py
"""
import json
import os
from pathlib import Path

import joblib
import numpy as np
from dotenv import load_dotenv

load_dotenv()
PASTA = Path(__file__).parent / "dados"
ARQ_LSA = PASTA / "modelo_lsa.joblib"


def _normalizar(m):
    m = np.asarray(m, dtype=float)
    return m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-9, None)


def usa_api() -> bool:
    return bool(os.getenv("EMBED_MODEL"))


def _api(textos):
    from openai import OpenAI
    cli = OpenAI(base_url=os.getenv("LLM_BASE_URL") or None, api_key=os.getenv("LLM_API_KEY") or "ollama")
    r = cli.embeddings.create(model=os.getenv("EMBED_MODEL"), input=textos)
    return [d.embedding for d in r.data]


def treinar_e_gerar(textos: list) -> np.ndarray:
    if usa_api():
        return _normalizar(_api(textos))
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import make_pipeline
    modelo = make_pipeline(TfidfVectorizer(strip_accents="unicode", ngram_range=(1, 2), sublinear_tf=True),
                           TruncatedSVD(n_components=min(24, len(textos) - 1), random_state=42))
    vet = modelo.fit_transform(textos)
    joblib.dump(modelo, ARQ_LSA)
    return _normalizar(vet)


def embedding_consulta(texto: str) -> np.ndarray:
    if usa_api():
        return _normalizar(_api([texto]))[0]
    try:
        modelo = joblib.load(ARQ_LSA)
    except Exception as e:  # ex.: versão diferente do scikit-learn
        raise RuntimeError("Modelo de embeddings incompatível. Rode: python embeddings.py && "
                           "python carregar_neo4j.py") from e
    return _normalizar(modelo.transform([texto]))[0]


if __name__ == "__main__":
    docs = json.loads((PASTA / "documentos.json").read_text(encoding="utf-8"))
    vet = treinar_e_gerar([f"{d['titulo']}. {d['texto']}" for d in docs])
    (PASTA / "embeddings.json").write_text(json.dumps({d["id"]: v.round(6).tolist() for d, v in zip(docs, vet)}))
    print(f"{len(docs)} embeddings com {vet.shape[1]} dimensões ({'API' if usa_api() else 'LSA local'}).")
