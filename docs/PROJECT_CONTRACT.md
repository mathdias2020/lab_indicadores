# Contrato do projeto

## Objetivo

Construir um laboratório que converta conhecimento discricionário e teorias tradicionais de mercado em indicadores testáveis, explicáveis e úteis para apoiar decisões de traders intermediários.

## Produto esperado

Cada indicador aprovado deve explicar:

- o que mede;
- quais dados sustentam a leitura;
- em que contexto deve ser aplicado;
- quando deve ser ignorado;
- quais limitações possui;
- qual evidência descritiva, preditiva e operacional existe;
- como pode contribuir para uma decisão sem virar uma ordem automática.

Além dos indicadores, o produto terá um painel de controle do laboratório. O painel permitirá acompanhar agentes, workers e pesquisas, iniciar novas execuções e consultar resultados. O chat e o painel serão interfaces diferentes para o mesmo control plane.

## Usuário inicial

Trader intermediário de WDO/WIN que quer aprender e operar melhor, sem depender de domínio prévio profundo de cada conceito.

## Regras de separação

- WDO e WIN nunca compartilham um veredito único.
- Os três horizontes são pesquisados separadamente.
- Fluxo, preço e combinação são trilhas diferentes.
- Indicadores atômicos são avaliados antes das confluências.
- Ausência de cobertura é `NULL` ou estado explícito de indisponibilidade, nunca zero.

## Gates

1. **Descritivo:** o evento é medido e reproduzido corretamente?
2. **Explicativo:** a ocorrência pode ser auditada por fatos e contexto?
3. **Preditivo:** existe resposta futura estável contra um baseline?
4. **Operacional:** a leitura melhora uma decisão em replay ou paper trading?

## Não-objetivos

- Criar um gerador de sinais.
- Escolher setups completos automaticamente.
- Usar retrospectivamente um resultado para mudar a hipótese.
- Misturar os contratos deste laboratório com o registro do outro laboratório.

## Critério de início do primeiro piloto

O piloto de absorção só avança quando os dados necessários, seus timestamps, a cobertura histórica e a reconstrução em replay estiverem auditados.
