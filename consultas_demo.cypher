// =====================================================================
// CONSULTAS PARA A DEMONSTRAÇÃO NO NEO4J BROWSER (http://localhost:7474)
// Copie uma de cada vez. As que retornam caminhos (p) aparecem como GRAFO.
// =====================================================================

// 0) Ver o esquema do grafo (rótulos e relações)
CALL db.schema.visualization();

// 0b) Ver constraints e índices (B-Tree/Range, Fulltext e Vetorial)
SHOW CONSTRAINTS;
SHOW INDEXES YIELD name, type, labelsOrTypes, properties, state;

// 1) A conta mula e as vítimas que relataram golpe (relação extraída do TEXTO)
MATCH p = (v:Cliente)-[:RELATOU_GOLPE]->(c:Conta {id: 'CT-0162'})<-[:POSSUI]-(dono:Cliente)
RETURN p;

// 2) Identidade compartilhada: quem usa o mesmo celular, telefone ou endereço que o dono da mula?
MATCH p = (:Cliente {nome: 'Dom Vasconcelos'})-[:USA_DISPOSITIVO|TEM_TELEFONE|MORA_EM]->(x)<-[:USA_DISPOSITIVO|TEM_TELEFONE|MORA_EM]-(outro:Cliente)
RETURN p;

// 3) Para onde foi o dinheiro da mula? (multi-hop: 1 a 3 transferências)
MATCH p = (:Conta {id: 'CT-0162'})-[:TRANSFERIU*1..3]->(destino:Conta)
WHERE all(t IN relationships(p) WHERE t.valor >= 5000)
RETURN p LIMIT 25;

// 4) Ciclo de lavagem (dinheiro volta para a origem)
MATCH p = (a:Conta)-[:TRANSFERIU*3..5]->(a)
WHERE all(t IN relationships(p) WHERE t.valor >= 20000)
RETURN p;

// 5) Caminho mais curto entre uma vítima e o fim do ciclo de lavagem
MATCH (v:Cliente)-[:RELATOU_GOLPE]->(:Conta) WITH v LIMIT 1
MATCH (g:Cliente {nome: 'Gabriela Marques'})
MATCH p = shortestPath((v)-[:POSSUI|TRANSFERIU|RELATOU_GOLPE|USA_DISPOSITIVO|TEM_TELEFONE|MORA_EM*..8]-(g))
RETURN p;

// 6) Busca VETORIAL nos documentos (precisa do vetor da pergunta; o app faz isso automaticamente)
//    Exemplo usando o embedding de um documento existente como "pergunta":
MATCH (d:Documento {id: 'DOC-001'})
CALL db.index.vector.queryNodes('doc_embedding', 5, d.embedding) YIELD node, score
RETURN node.id, node.titulo, round(score, 3) AS similaridade;

// 7) Busca FULLTEXT
CALL db.index.fulltext.queryNodes('doc_texto', 'golpe~ central') YIELD node, score
RETURN node.id, node.titulo, score LIMIT 5;

// 8) Busca HÍBRIDA: documento -> entidades citadas -> vizinhança no grafo
CALL db.index.fulltext.queryNodes('doc_texto', 'falso funcionário') YIELD node AS doc
MATCH p = (doc)-[:MENCIONA]->(e)-[*1..2]-(x)
WHERE NOT x:Documento
RETURN p LIMIT 40;

// 9) Comunidades (Louvain) e PageRank já gravados como propriedades
MATCH (c:Cliente) WITH c.comunidade AS comunidade, collect(c.nome) AS membros
RETURN comunidade, size(membros) AS tamanho, membros ORDER BY tamanho LIMIT 5;

MATCH (c:Conta)<-[:POSSUI]-(cli) RETURN c.id, cli.nome, round(c.pagerank, 4) AS pagerank
ORDER BY pagerank DESC LIMIT 10;

// 10) (OPCIONAL, só com o plugin GDS - docker-compose já instala) mesmos algoritmos no Neo4j
CALL gds.graph.project('transferencias', 'Conta', {TRANSFERIU: {properties: 'valor'}});
CALL gds.pageRank.stream('transferencias', {relationshipWeightProperty: 'valor'})
YIELD nodeId, score RETURN gds.util.asNode(nodeId).id AS conta, round(score, 4) AS score
ORDER BY score DESC LIMIT 10;
CALL gds.graph.drop('transferencias');
