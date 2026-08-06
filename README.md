# Laboratório de Indicadores WDO/WIN

Laboratório de pesquisa aplicada para transformar conceitos de mercado em indicadores mensuráveis, explicáveis e validados.

## Escopo

- Fluxo, preço e fluxo + preço.
- WDO e WIN, mantendo os ativos separados.
- Scalping, intraday tático e intraday amplo, mantendo os horizontes separados.
- Indicadores atômicos, contexto, explicação, validação e mapa de aplicação.
- Absorção como primeiro piloto metodológico.

## Control plane

O laboratório terá um painel conectado ao banco para acompanhar agentes, workers e pesquisas, iniciar novas execuções e consultar resultados, logs e artefatos.

O painel e o chat serão duas interfaces para o mesmo control plane. Nenhum deles executará código diretamente: ambos registrarão comandos idempotentes para o orquestrador.

## Fora do escopo

- Estratégias completas de entrada, stop e alvo.
- Envio ou automação de ordens.
- Robôs MT5.
- Promessa de lucro.
- Uso de um LLM como calculadora ou autoridade estatística.

## Princípio operacional

O laboratório separa:

1. Fato observado.
2. Interpretação do fenômeno.
3. Hipótese preditiva.
4. Utilidade operacional.

Um indicador só pode avançar de uma camada para a seguinte depois de passar pelo gate correspondente.

## Estado atual

- Fase: fundação e contratos.
- VPS e Supabase: infraestrutura compartilhada, sem alterações deste laboratório.
- Parquets: entrada global comum, sempre montada como somente leitura; nenhum
  laboratório escreve nela.
- Código, DuckDBs, resultados, logs e artefatos: exclusivos de cada laboratório.

## Documentos

- [`PROJECT_CONTRACT.md`](docs/PROJECT_CONTRACT.md)
- [`RESEARCH_PROTOCOL.md`](docs/RESEARCH_PROTOCOL.md)
- [`INDICATOR_CONTRACT.md`](docs/INDICATOR_CONTRACT.md)
- [`SHARED_INFRA_CONTRACT.md`](docs/SHARED_INFRA_CONTRACT.md)
- [`CONTROL_PLANE.md`](docs/CONTROL_PLANE.md)
- [`VPS_REGISTRATION.md`](docs/VPS_REGISTRATION.md)
