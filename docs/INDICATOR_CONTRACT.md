# Contrato de indicador

## Identidade

- `indicator_id`:
- `name`:
- `version`:
- `concept_family`:
- `status`:

## Pergunta

O indicador deve responder uma pergunta principal, expressa em uma frase verificável.

## Dados

- fontes;
- colunas;
- unidades;
- timezone;
- granularidade;
- timestamp de disponibilidade;
- cobertura esperada;
- tratamento de dados ausentes.

## Cálculo

- fórmula;
- janelas;
- estados;
- thresholds pré-registrados;
- dependências;
- invariantes e testes de fronteira.

## Explicação

Cada ocorrência deve conseguir informar:

- onde e quando ocorreu;
- quais fatos sustentaram a detecção;
- qual contexto estava presente;
- o que reforça a leitura;
- o que a invalida;
- o que ainda não pode ser concluído.

## Validação

- baseline;
- respostas futuras;
- métricas;
- estabilidade;
- controles nulos;
- limitações;
- critério de promoção;
- holdout acessado ou não.

## Aplicação

O indicador pode informar uma decisão possível, mas não deve emitir ordem. A saída deve distinguir:

- observar;
- aguardar confirmação;
- considerar cenário;
- evitar;
- sem cobertura;
- evidência insuficiente.

## Exemplo inicial: absorção

Hipótese de trabalho: agressão relevante encontrou resistência sem produzir deslocamento proporcional do preço.

Ramificações posteriores permitidas somente após o evento básico ser auditado:

- repetição no nível;
- persistência;
- concentração por player;
- saldo agressivo acumulado observado;
- localização no dia;
- leilão;
- RLP;
- trade direto;
- resposta futura.

