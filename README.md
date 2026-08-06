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

- Fase: control plane v1 e preflight reproduzível.
- VPS e Supabase: infraestrutura compartilhada; este laboratório usa apenas seu
  caminho, schema e credenciais lógicas exclusivos.
- Parquets: entrada global comum, sempre montada como somente leitura; nenhum
  laboratório escreve nela.
- Código, DuckDBs, resultados, logs e artefatos: exclusivos de cada laboratório.
- O schema `lab_indicadores` e seu contrato de fila já foram aplicados no
  Supabase compartilhado. O painel MVP está em [`dashboard/`](dashboard/),
  usando Auth e RPCs autenticadas, e está publicado na Vercel em
  <https://lab-indicadores-kappa.vercel.app>.
- O orquestrador host-side está instalado como serviço systemd, conectado ao
  Supabase com usuário PostgreSQL exclusivo e ativo no boot.

## Documentos

- [`PROJECT_CONTRACT.md`](docs/PROJECT_CONTRACT.md)
- [`RESEARCH_PROTOCOL.md`](docs/RESEARCH_PROTOCOL.md)
- [`INDICATOR_CONTRACT.md`](docs/INDICATOR_CONTRACT.md)
- [`SHARED_INFRA_CONTRACT.md`](docs/SHARED_INFRA_CONTRACT.md)
- [`CONTROL_PLANE.md`](docs/CONTROL_PLANE.md)
- [`CONTROL_PLANE_V1.md`](docs/CONTROL_PLANE_V1.md)
- [`ORCHESTRATOR.md`](docs/ORCHESTRATOR.md)
- [`VPS_REGISTRATION.md`](docs/VPS_REGISTRATION.md)
- [`dashboard/README.md`](dashboard/README.md)
