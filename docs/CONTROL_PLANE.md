# Control plane do laboratório

## Objetivo

O painel é a interface de operação do laboratório. Ele não é apenas uma tela de monitoramento: é o ponto de entrada controlado para solicitar pesquisas, acompanhar execuções e consultar evidências.

## Entradas de comando

Existirão duas entradas com o mesmo contrato:

1. Painel web.
2. Chat assistido.

Nenhuma delas deve executar comandos diretamente no sistema operacional. Ambas registram uma solicitação estruturada no control plane, que o orquestrador valida e encaminha ao worker.

## Fluxo de uma pesquisa

```text
usuário solicita pesquisa
→ comando idempotente registrado
→ contrato e permissões validados
→ worker reivindica o comando
→ execução cria checkpoints
→ eventos e métricas são registrados
→ artefatos são armazenados fora do Postgres
→ painel e chat consultam o resultado
```

## Estados mínimos

- `queued`
- `claimed`
- `running`
- `succeeded`
- `failed`
- `cancel_requested`
- `cancelled`

Uma execução deve registrar dataset, manifesto, configuração, versão do código, worker, heartbeat, erro, horários e artefatos produzidos.

## Comandos iniciais

- iniciar pesquisa;
- solicitar cancelamento;
- consultar status;
- abrir resultado;
- abrir artefato;
- consultar logs;
- comparar execuções.

## Regras de segurança

- O chat não recebe shell irrestrito.
- Comandos precisam de chave de idempotência.
- Um worker só reivindica comandos compatíveis com suas capacidades.
- O cancelamento é cooperativo e registrado.
- Nenhuma pesquisa altera dados brutos.
- Ações destrutivas ou de infraestrutura ficam fora do painel inicial.

## Separação deste laboratório

O control plane deste projeto não reutilizará as tabelas do schema `lab_automatizado` do outro laboratório. A área própria será criada somente após revisão do contrato compartilhado e migração explícita.
