# Hermes no laboratório de indicadores

## Estado desta integração

O laboratório possui o agente lógico `hermes-indicadores`, registrado no
control plane e acompanhado pelo dashboard. A integração está em duas camadas:

- `lab-indicadores-hermes-runtime.service`: heartbeat contínuo e observação do
  dataset de desenvolvimento;
- `lab-indicadores-hermes-engine.service`: serviço `oneshot` que consome um job
  isolado e produz uma proposta versionada para revisão.

O primeiro job habilitado é um fixture reproduzível para a hipótese de
absorção em WDO, fluxo + preço e intraday tático. Ele não é uma conclusão
estatística nem uma promessa de resultado operacional.

## Análise determinística

O baseline descritivo agora pode ser executado a partir de uma proposta em
revisão. O worker isolado usa DuckDB e o contexto
`hermes-context/absorption-analysis-v1.json` para analisar o Parquet de WDO de
2016-01 em janelas de 60 segundos.

O relatório registra agressão compradora/vendedora, deslocamento, concentração
por agente agressor, persistência no nível, leilão e cross trade. Passivo, RLP
e semântica completa de trade direto permanecem explicitamente sem cobertura.
O resultado é `descriptive` e não calcula retorno futuro.

## Contratos preservados

- holdout sempre fechado (`holdout_accessed: false`);
- somente o contexto versionado `hermes-context-absorption-v1` pode iniciar
  esta pesquisa;
- propostas são hipóteses, com pergunta, mecanismo, plano de validação,
  limitações, contexto e hashes;
- o artefato é escrito apenas em
  `/srv/labs/projects/lab-b/hermes/outbox/proposals`;
- runtime e engine não recebem Supabase, rede, Docker socket ou capacidade de
  executar ordens;
- o banco registra a proposta em `lab_indicadores.proposals` com estado inicial
  `in_review` e evidência `not_tested`;
- aceitação, rejeição e qualquer mudança de definição continuam sendo revisão
  humana explícita.

## Fluxo controlado

1. Usuário autenticado aciona “Gerar hipótese” no dashboard.
2. `dashboard_enqueue_research` cria uma run e um comando idempotentes.
3. O orquestrador reivindica o comando e grava um job no inbox do Hermes.
4. O serviço oneshot valida o contexto e escreve o JSON canônico da proposta.
5. O orquestrador valida o hash, registra a proposta e o artefato no Supabase.
6. O dashboard exibe a proposta como “em revisão”.
7. O usuário pode acionar a baseline descritiva; o worker grava um relatório
   hashado e o dashboard acompanha a run de análise.

O mesmo contrato poderá receber um provider analítico posteriormente, mas o
provider LLM não é ativado nesta fase. A ativação dependerá de credencial
específica, teste de saída estruturada, custo e revisão do isolamento.

## Fronteiras

| Item | Este laboratório | Outro laboratório |
| --- | --- | --- |
| agente | `hermes-indicadores` | exclusivo do outro projeto |
| diretório | `/srv/labs/projects/lab-b/hermes` | não tocar |
| tabela | `lab_indicadores.agents` e `lab_indicadores.proposals` | não tocar |
| serviços | `lab-indicadores-*` | não tocar |
| dados | `/srv/labs/datasets`, leitura | `/srv/labs/datasets`, leitura |
