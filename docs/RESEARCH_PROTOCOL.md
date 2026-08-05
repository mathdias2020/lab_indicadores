# Protocolo de pesquisa

Toda pesquisa deve possuir um manifesto antes de consultar o resultado.

## Manifesto mínimo

- `research_id` único;
- pergunta de pesquisa;
- mecanismo econômico alegado;
- ativo;
- horizonte;
- conceito e indicador;
- período elegível;
- dados e manifestos utilizados;
- timestamp de disponibilidade;
- features permitidas;
- variantes enumeradas;
- baseline;
- controles e nulos;
- métrica primária;
- critério de promoção;
- holdout protegido;
- limitações conhecidas.

## Tipos de evidência

### Descritiva

Verifica se o indicador representa o fenômeno alegado, sua frequência, cobertura, estabilidade e interpretabilidade.

### Preditiva

Verifica resposta futura em horizontes pré-declarados, comparada a baseline pareado, controles nulos e estabilidade por período e regime.

### Operacional

Verifica se o indicador melhora seleção, timing, espera, invalidação ou gerenciamento em replay ou paper trading, considerando latência e custos.

## Regras de causalidade

- Uma ocorrência em `t` só pode usar informação disponível até `t`.
- Features e rótulos futuros devem ser separados.
- O fechamento de uma janela não pode aparecer antes de estar disponível.
- Nenhuma variante nova pode ser criada depois de observar o resultado.
- Cada execução registra hash, parâmetros, versão e número de tentativas.

## Estados possíveis

- `IDEIA`
- `PREREGISTRADA`
- `DADOS_AUDITADOS`
- `EXECUTADA`
- `EM_REVISAO`
- `VALIDADA_DESCRITIVA`
- `VALIDADA_PREDITIVA`
- `VALIDADA_OPERACIONAL`
- `REJEITADA`
- `SEM_COBERTURA`

