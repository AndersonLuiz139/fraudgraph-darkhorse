# Guia da Apresentação — FraudGraph

## Para a planilha do professor (item 02 do quadro)
- **Tema dos dados em grafo:** Dados financeiros: detecção de fraudes (GraphRAG + KAG)
- **Nome do grupo:** Dark Horse
- **Integrantes:** Anderson Luiz Gomes Soares, Aderbal Ferreira da Silva Filho, Lucas Nascimento de Figueiredo, João Victor de Almeida Quintans Urbano Serra Pinto, Ruann Pablo da Silva Pinheiro, Leodymann Hugo Silva do Nascimento, Davi Meira Villar
- **Horário:** já combinado com o professor

---

## Checklist de HOJE À NOITE (não deixe para amanhã!)
1. Instalar Python 3.11+ e rodar `pip install -r requirements.txt`.
2. Subir o Neo4j (Docker, Desktop ou Aura) e rodar `python carregar_neo4j.py`. Deve terminar com "Pronto!".
3. Rodar `streamlit run app.py` e clicar em todas as abas.
4. (Opcional) Criar a chave grátis do Gemini e colocar no `.env`.
5. Rodar `pytest -q`. Mostrar "17 passed" na apresentação vale ponto em "qualidade do código".
6. **Plano B:** se o Neo4j falhar na hora, desligue o botão "Usar Neo4j". O app continua funcionando offline.
7. Abrir o Neo4j Browser (http://localhost:7474) com o arquivo `consultas_demo.cypher` à mão.

---

## Roteiro (~15 minutos, 7 pessoas)

| # | Quem | Tempo | Slides | Conteúdo |
|---|---|---|---|---|
| 1 | Anderson Luiz | 1 min | 1–2 | Abertura e o problema da fraude no Brasil |
| 2 | Aderbal Ferreira | 2 min | 4–5 | Limites dos LLMs e a comparação RAG × GraphRAG × KAG |
| 3 | Lucas Nascimento | 2 min | 6–7 | Como aplicamos o KAG e a arquitetura nos 4 pilares |
| 4 | João Victor | 2 min | 8–9 | Modelo LPG no Neo4j e o pipeline Text-to-Graph |
| 5 | Ruann Pablo | 2 min | 3 e 10 | Por que fraude é relacional (slide 3) e as 4 regras (slide 10) |
| 6 | Leodymann Hugo | 5 min | 11 | **DEMO ao vivo** (roteiro abaixo) |
| 7 | Davi Meira | 2 min | 12–14 | Resultados, RAG × KAG na mesma pergunta e conclusão |

A divisão é uma sugestão: troquem à vontade. Quem fizer a demo precisa ter rodado o sistema antes.
As falas de cada pessoa estão no documento "Falas da apresentação — Grupo Dark Horse" e nas notas de cada slide.

---

## Roteiro da DEMO (5 minutos)
1. **Aba "Visão geral":** mostre os números, o modelo LPG e a tabela de comunidades. Fale: "uma comunidade de 6 e
   outra de 3 pessoas. O Louvain achou essas redes sem saber o que é fraude."
2. **Aba "Text-to-Graph":** abra o DOC-001. Mostre "D. Vasconcelos" resolvido para o mesmo cliente que "DOM VASCONCELOS".
   Use o **teste ao vivo** com o texto que já vem preenchido.
3. **Aba "Regras (KAG)":** abra a R3 (conta mula) e clique em "Ver subgrafo". Depois role até
   "Os alertas estão ligados?" para mostrar a quadrilha de 6.
4. **Aba "Perguntar":** escolha "Os alertas estão ligados entre si? Existe uma quadrilha?" e marque **Comparar com RAG**.
   - Esquerda (KAG): plano lógico, documentos, subgrafo, resposta, **confiança** e **rastreabilidade** (Cypher executado).
   - Direita (RAG): só extratos soltos. "O RAG tradicional não enxerga a rede."
5. (Se der tempo) **Neo4j Browser:** cole a consulta 1 e depois a 4 do arquivo `consultas_demo.cypher`. O grafo visual impressiona.

---

## Como explicar cada arquivo (cada integrante deve dominar o seu)
- **gerar_dados.py:** "Criamos uma base fictícia: 150 pessoas, 10 empresas, ~1.500 transações e 26 textos. Plantamos
  duas redes de fraude e guardamos o gabarito separado, para provar que o sistema encontra sozinho."
- **extracao.py:** "Lê textos livres e acha nomes, contas, telefones e valores (NER). Depois liga cada menção ao nó certo
  (Resolução de Entidades): 'D. Vasconcelos' e 'DOM VASCONCELOS' viram o mesmo cliente. Telefone em 4 formatos vira um só."
- **carregar_neo4j.py:** "Cria as constraints de unicidade, os índices Range (B-Tree), Fulltext e Vetorial, e carrega
  tudo com UNWIND em lotes."
- **algoritmos.py:** "PageRank mostra para onde o dinheiro converge. Louvain agrupa clientes muito ligados entre si."
- **conhecimento.py:** "Aqui está o 'K' do KAG: o esquema e as regras de especialistas, escritas em Cypher."
- **motor.py:** "A busca híbrida: acha documentos por vetor, pega as entidades citadas e percorre o grafo 2 saltos.
  Depois aplica as regras e calcula a confiança."
- **llm.py:** "O LLM decompõe a pergunta em um plano lógico, gera Cypher e escreve a resposta citando os caminhos.
  É proibido inventar."
- **app.py:** "A interface em Streamlit, com o subgrafo interativo, o Cypher executado e a confiança."

---

## Perguntas prováveis do professor (e respostas)
**1. Qual a diferença entre GraphRAG e KAG?**
GraphRAG recupera um subgrafo como contexto. KAG vai além: usa um esquema e regras de domínio (conhecimento de especialista),
decompõe a pergunta em um plano lógico e combina grafo com texto nos dois sentidos (mutual indexing). No nosso
projeto, as regras R1–R4 e o plano lógico são a parte KAG.

**2. Como vocês reduzem alucinação?**
(a) O LLM só recebe fatos vindos do grafo. (b) A resposta precisa citar caminho, documento ou regra entre colchetes.
(c) O Cypher gerado é bloqueado se tiver escrita. (d) A confiança é calculada pelas evidências, não pelo LLM.
(e) A tela mostra tudo o que foi consultado.

**3. Por que não usaram o plugin GDS?**
Para funcionar também no Aura Free, que não tem o GDS. Calculamos PageRank e Louvain com NetworkX e gravamos como
propriedades. Com GDS seria `gds.pageRank.write`. O `docker-compose.yml` já instala o plugin, e há um exemplo em `consultas_demo.cypher`.

**4. Os dados são reais?**
Não. São fictícios, gerados com o Faker, por causa da LGPD e do sigilo bancário. Os padrões de fraude seguem tipologias
conhecidas: conta mula, smurfing, lavagem circular e identidades sintéticas.

**5. Como sabem que funciona?**
Temos um gabarito e 17 testes automatizados. Todas as fraudes plantadas foram encontradas, sem falsos positivos.
As lojas recebem de muitas pessoas e não foram marcadas como mula.

**6. Como é calculada a confiança?**
35% vem da similaridade dos documentos, 40% de quantas regras independentes confirmaram e 25% do tamanho do subgrafo
de evidências. O resultado vira Alta, Média ou Baixa.

**7. E em produção?**
Seria preciso usar dados reais anonimizados, ingestão em streaming (Kafka), embeddings melhores, revisão humana
dos alertas, ajuste de limiares com dados históricos e comunicação ao COAF.

**8. Limitações?**
Os dados são sintéticos e pequenos. O extrator por regras depende de padrões de escrita. Os limiares foram definidos
manualmente. O LSA é simples perto de embeddings neurais.
