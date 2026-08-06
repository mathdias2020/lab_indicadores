# Baseline descritiva de absorção por ativo

## O que foi executado

O laboratório agora aceita três contextos explícitos:

- `absorption-descriptive-baseline-v1`: baseline histórica WDO de uma amostra mensal;
- `absorption-descriptive-multi-period-wdo-v1`: WDO em janeiro de 2016, 2020 e 2024;
- `absorption-descriptive-multi-period-win-v1`: WIN em janeiro de 2016, 2020 e 2024.

Cada contexto possui manifesto, caminhos, bytes e SHA-256 próprios. O worker verifica o ativo, a versão, a integridade do arquivo, o modo somente leitura e a fronteira do holdout antes de abrir o Parquet.

## O que o relatório mede

Em janelas de 60 segundos, o worker calcula agressão compradora/vendedora, agressão líquida e absoluta, deslocamento e range do preço, concentração HHI do agente agressor, persistência do nível, leilão, cross trade e contagem de negócios. Os limiares são pré-registrados e pooled dentro da amostra declarada; o relatório também separa a cobertura por período.

## Limite da evidência

O resultado é `descriptive`, não é backtest, não possui label futuro, não prova direção, entrada, stop, alvo ou lucro. A amostra é representativa por fatias mensais, não um censo do ano. Passivo, RLP e a semântica completa de trades diretos não estão no contrato bruto usado nesta etapa e permanecem `NULL`/separados.

2025 e 2026 continuam fechados. A próxima promoção só pode ocorrer depois de um estudo preditivo pré-registrado com markout futuro, baseline pareado, controles nulos, walk-forward e gate formal.
