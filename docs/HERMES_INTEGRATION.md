# Hermes no laboratório de indicadores

## Ciclo com entendimento dos dados

O painel pode iniciar uma campanha WDO ou WIN. A campanha executa, nesta ordem:

1. `data_profile`: o worker DuckDB lê somente os arquivos de desenvolvimento declarados no contexto e grava schema, cobertura, tipos de negócio, datas, faixa de preço/quantidade, agentes distintos e campos disponíveis;
2. `exploration` (quando o provider é OpenAI): Hermes escolhe zero a três perguntas de um catálogo semântico. O worker as executa com DuckDB em modo somente leitura e devolve agregados limitados; Hermes nunca envia SQL nem recebe linhas brutas;
3. `hypothesis`: Hermes recebe o perfil hashado e, se houver, o relatório agregado de exploração antes da proposta. Nenhuma dessas etapas abre o holdout;
4. `gate`: a proposta fica `in_review`. Aceitação e execução da análise continuam humanas;
5. `analysis`: o worker executa o cálculo determinístico e registra relatório, hash, cobertura, limites e holdout fechado.

Uma falha de análise não altera a proposta anterior. O orquestrador pode criar uma etapa `error_review` limitada por `max_iterations`, vinculada à run falha, ao erro, à proposta pai e ao mesmo perfil de dados. O Hermes grava uma nova proposta com `revision_no` e `change_kind=error_review`; nenhuma revisão é promovida ou executada automaticamente.

O worker e os serviços do laboratório não montam `/srv/labs/datasets/holdout`. Todos os perfis e propostas continuam dentro de `/srv/labs/projects/lab-b`.

O catálogo exploratório atual é deliberadamente pequeno: distribuição por
tipo de trade, atividade horária, rajadas de um minuto e concentração por
agente. A consulta do modelo é convertida pelo código em SQL fixo; são no
máximo três consultas, 100 linhas agregadas por consulta e 31 dias por janela.
Cada execução gera `hermes_exploration` com hash lógico, hash do arquivo,
manifesto, plano, perguntas, resultados e prova de que não houve acesso ao
holdout nem modificação do canônico.

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

O provider padrão continua sendo o fixture reproduzível até que uma credencial
seja provisionada e `openai` seja selecionado no painel. A ativação exige teste
de saída estruturada, custo e revisão do isolamento.

## Provider OpenAI

O painel controla o provider (`fixture` ou `openai`), o modelo GPT-5.6 e o
nível de raciocínio para os próximos ciclos. A configuração é registrada em
`lab_indicadores.ai_provider_settings` e lida pelo orquestrador ao criar cada
job Hermes.

Quando `openai` é selecionado, o engine usa a Responses API com Structured
Outputs. O modelo recebe o contexto versionado, o perfil agregado e, em uma
revisão, a hipótese pai e o erro registrado. O ativo, a trilha, o horizonte, o
manifesto, os gates e o holdout continuam sendo invariantes do código; a saída
do modelo não pode alterá-los.

A chave não é armazenada no GitHub, no navegador ou no Supabase. Ela deve ser
provisionada na VPS como `OPENAI_API_KEY` em
`/etc/lab-indicadores/openai.env`, com permissão 600. O painel exibe e altera
o modelo, mas nunca recebe o valor do segredo.

## Fronteiras

| Item | Este laboratório | Outro laboratório |
| --- | --- | --- |
| agente | `hermes-indicadores` | exclusivo do outro projeto |
| diretório | `/srv/labs/projects/lab-b/hermes` | não tocar |
| tabela | `lab_indicadores.agents` e `lab_indicadores.proposals` | não tocar |
| serviços | `lab-indicadores-*` | não tocar |
| dados | `/srv/labs/datasets`, leitura | `/srv/labs/datasets`, leitura |
