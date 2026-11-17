# FraudGraph — Detecção de Fraudes Financeiras com GraphRAG + KAG

Grupo **Dark Horse**. Projeto Final da disciplina **Tendências em Ciência da Computação**, prof. Ricardo Roberto de Lima, 2026.
**Integrantes:** Anderson Luiz Gomes Soares, Aderbal Ferreira da Silva Filho, Lucas Nascimento de Figueiredo, João Victor de Almeida Quintans Urbano Serra Pinto, Ruann Pablo da Silva Pinheiro, Leodymann Hugo Silva do Nascimento, Davi Meira Villar.
**Domínio:** dados financeiros (fraudes). **Stack:** Neo4j + Python + Streamlit + LLM.

> Fraude financeira é um problema **relacional**. Uma transação de R$ 900 não parece suspeita sozinha.
> Ela só fica suspeita quando o grafo mostra que a conta de destino foi aberta há 2 meses, recebeu de 14 vítimas,
> repassou 92% em poucas horas e pertence a alguém que divide o celular com outras 4 pessoas.

---

## 1. Como rodar (passo a passo)

### 1.1. Pré-requisitos
- Python **3.11+**
- Neo4j 5 (escolha uma opção):
  - **Docker:** `docker compose up -d`. Já sobe com usuário `neo4j` e senha `senha12345`.
  - **Neo4j Desktop:** crie um banco local com senha `senha12345`.
  - **Neo4j Aura Free** (nuvem, grátis): crie a instância e copie URI, usuário e senha para o `.env`.

### 1.2. Instalação
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    |   Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
copy .env.exemplo .env      # Linux/Mac: cp .env.exemplo .env   (depois edite o .env)
```

### 1.3. Pipeline completo
Os dados já vêm gerados na pasta `dados/`. Rodar o passo 1 de novo pode mudar os nomes se a versão do Faker for outra.
```bash
python gerar_dados.py        # 1. gera os dados fictícios (CSV + documentos de texto + gabarito)
python extracao.py           # 2. Text-to-Graph: NER + RE + Resolução de Entidades (LLM se configurado)
python embeddings.py         # 3. embeddings dos documentos (para o índice vetorial)
python carregar_neo4j.py     # 4. schema LPG + índices + carga + PageRank/Louvain no Neo4j
streamlit run app.py         # 5. interface
pytest -q                    # testes operacionais (17 testes, não precisam de Neo4j)
```

**Plano B:** se o Neo4j não estiver disponível, o app entra sozinho no **modo offline**. Ele monta o mesmo grafo em
memória com NetworkX. Também dá para forçar esse modo pelo botão "Usar Neo4j" na barra lateral.

**Sem LLM:** tudo funciona, e as respostas saem por template. Com LLM (Gemini grátis, OpenAI ou Ollama local), o
sistema gera o plano lógico, gera Cypher (Text2Cypher), extrai entidades e escreve respostas citando os caminhos.

---

## 2. Arquitetura (os 4 pilares exigidos)

```
 Documentos (texto)     CSV estruturado
        │                     │
        ▼                     │
 ┌──────────────────┐         │
 │ 1. Text-to-Graph │ NER (regex+dicionário ou LLM) → RE → Resolução de Entidades
 └────────┬─────────┘         │
          ▼                   ▼
 ┌──────────────────────────────────┐
 │ 2. Neo4j LPG                     │ constraints + índices Range/Fulltext/Vetorial
 └────────┬─────────────────────────┘ + PageRank e Louvain gravados como propriedade
          ▼
 ┌──────────────────────────────────┐
 │ 3. Recuperação híbrida (KAG)     │ vetor → sementes → Cypher multi-hop → regras de
 └────────┬─────────────────────────┘ especialista → LLM com grounding (cita caminhos)
          ▼
 ┌──────────────────────────────────┐
 │ 4. Streamlit + PyVis (vis.js)    │ subgrafo, Cypher executado, nós consultados, confiança
 └──────────────────────────────────┘
```

| Arquivo | Pilar | O que faz |
|---|---|---|
| `gerar_dados.py` | dados | Gera 169 clientes, 1.489 transações e 26 documentos de texto, com 2 redes de fraude plantadas |
| `extracao.py` | 1 | NER, RE e Resolução de Entidades (nomes abreviados, maiúsculas, telefones em 4 formatos) |
| `embeddings.py` | 2/3 | Embeddings dos documentos (API ou LSA local) |
| `carregar_neo4j.py` | 2 | Constraints, índices (Range, Fulltext, Vetorial), carga com `UNWIND`, algoritmos |
| `algoritmos.py` | 3 | PageRank (contas) e Louvain (clientes) |
| `conhecimento.py` | 3 (KAG) | Esquema do grafo + 4 regras de especialista em Cypher |
| `motor.py` | 3 | Recuperação híbrida, multi-hop, caminho mais curto, confiança. Motores Neo4j e offline |
| `llm.py` | 3 | Plano lógico (logical form), Text2Cypher, resposta com grounding |
| `rag_texto.py` | comparação | RAG tradicional (só texto) para mostrar a diferença |
| `app.py` | 4 | Interface com 5 abas |
| `consultas_demo.cypher` | demo | Consultas prontas para mostrar no Neo4j Browser |
| `tests/` | qualidade | 17 testes operacionais |

### Modelo LPG
```
(:Cliente)-[:POSSUI]->(:Conta)-[:TRANSFERIU {valor, data_hora, canal}]->(:Conta)
(:Cliente)-[:USA_DISPOSITIVO]->(:Dispositivo)
(:Cliente)-[:TEM_TELEFONE]->(:Telefone)
(:Cliente)-[:MORA_EM]->(:Endereco)
(:Cliente)-[:RELATOU_GOLPE {valor, doc}]->(:Conta)        ← extraída do texto
(:Documento {texto, embedding})-[:MENCIONA {papel}]->(:Cliente|:Conta|:Telefone)
```

### Regras de especialista (o "K" do KAG)
| Regra | Padrão de fraude | Lógica |
|---|---|---|
| R1 | Identidade compartilhada | 3 ou mais clientes com o mesmo telefone, endereço ou celular |
| R2 | Ciclo de lavagem | ≥ R$ 20 mil passando por 3 a 5 contas e voltando à origem em ≤ 72h |
| R3 | Conta mula | conta nova que recebe de 8 ou mais contas e repassa 80% ou mais |
| R4 | Fracionamento (smurfing) | 4 ou mais transferências entre 90% e 100% do limite interno de alerta |

---

## 3. Resultados (verificados pelos testes)
- **Text-to-Graph:** 57 de 57 menções resolvidas. 14 grafias diferentes de nomes viraram 12 pessoas únicas.
- **Regras:** todas as fraudes plantadas foram encontradas, **sem falsos positivos**. Lojas recebem de muitas contas, mas não
  entram na R3 porque não repassam quase tudo. Compras de valor alto e legítimas não entram na R2.
- **Multi-hop:** os 4 alertas, que pareciam isolados, convergem em **uma única quadrilha de 6 pessoas**. Uma delas não
  divide nenhum identificador com as outras e só aparece pelo caminho do dinheiro.
- **Louvain:** separou as duas redes de fraude em comunidades próprias, sem receber nenhuma regra.
- **RAG × KAG:** para "Existe uma quadrilha?", o RAG tradicional devolve extratos soltos e não conclui nada.
  O KAG responde com nomes, contas, valores e o caminho no grafo que prova cada afirmação.

---

## 4. Créditos e uso de IA
- Bibliotecas: Neo4j Python Driver, Streamlit, NetworkX, PyVis (vis.js), scikit-learn, Faker, OpenAI SDK.
- Conceito KAG: Liang et al., *KAG: Boosting LLMs in Professional Domains via Knowledge Augmented Generation*,
  Ant Group, 2024 (arXiv:2409.13731).
- **Uso de IA:** o desenvolvimento contou com o apoio de um assistente de IA (Claude, da Anthropic) para estruturar o
  código e a documentação. A equipe revisou, testou e adaptou todo o material.
- Todos os dados são **fictícios**: nomes, CPFs, telefones e contas foram gerados pela biblioteca Faker.
