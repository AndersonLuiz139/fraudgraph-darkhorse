"""
RAG tradicional (para comparação): transforma os dados em FRASES de texto
e recupera as mais parecidas com a pergunta (TF-IDF, similaridade de cosseno).

Isso imita o que um RAG comum faz com documentos/extratos: busca por SEMELHANÇA DE TEXTO,
sem entender as LIGAÇÕES entre as entidades.
"""
from pathlib import Path

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

PASTA = Path(__file__).parent / "dados"


class BuscadorTexto:
    def __init__(self, pasta=PASTA):
        cli = pd.read_csv(pasta / "clientes.csv", dtype=str)
        ct = pd.read_csv(pasta / "contas.csv", dtype=str)
        tx = pd.read_csv(pasta / "transacoes.csv", dtype=str)
        tel = dict(pd.read_csv(pasta / "telefones.csv", dtype=str)[["id", "numero"]].values)
        end = pd.read_csv(pasta / "enderecos.csv", dtype=str).set_index("id")
        nome = dict(zip(cli["id"], cli["nome"]))
        titular = dict(zip(ct["id"], ct["cliente_id"].map(nome)))

        self.docs = []
        for c in cli.itertuples():
            e = end.loc[c.endereco_id]
            self.docs.append(f"Cadastro: {c.nome} ({c.tipo}), cliente desde {c.data_cadastro}, telefone "
                             f"{tel[c.telefone_id]}, mora em {e.logradouro}, {e.cidade}.")
        for t in tx.itertuples():
            v = f"{float(t.valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            self.docs.append(f"Extrato: em {t.data_hora[:16].replace('T', ' ')} a conta {t.origem} "
                             f"({titular[t.origem]}) enviou R$ {v} via {t.canal} para a conta {t.destino} "
                             f"({titular[t.destino]}).")
        self.vet = TfidfVectorizer(strip_accents="unicode", lowercase=True)
        self.matriz = self.vet.fit_transform(self.docs)

    def buscar(self, pergunta: str, k=8) -> list:
        sim = cosine_similarity(self.vet.transform([pergunta]), self.matriz).ravel()
        return [self.docs[i] for i in sim.argsort()[::-1][:k]]
