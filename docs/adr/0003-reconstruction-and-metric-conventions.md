# Convenções de métricas sobre posições analíticas

## Status

Accepted

## Decisão

As métricas são calculadas sob demanda sobre posições analíticas finalizadas, sem reconstruir ciclos. Métricas gerais incluem posições sem estratégia comprovada para preservar a reconciliação do resultado total; métricas filtradas por estratégia consideram somente posições associadas. Posições abertas continuam visíveis, mas não compõem métricas realizadas.

P&L é a soma do resultado líquido informado nas posições. Taxa de acerto é a quantidade de posições com P&L positivo dividida por todas as posições realizadas, incluindo resultados iguais a zero no denominador. Profit factor é o ganho bruto dividido pelo módulo da perda bruta; payoff é o ganho médio dividido pelo módulo da perda média; expectância é a média aritmética do P&L líquido. Métricas sem denominador válido retornam `null` com motivo explícito, nunca infinito.

Sharpe e Sortino por posição usam o P&L monetário líquido, sem anualização. As variantes diárias usam o retorno diário da amostra filtrada: P&L das posições encerradas no dia dividido pelo saldo de abertura ajustado. Esse saldo é o capital contábil após o único ajuste de abertura e antes dos resultados operacionais do dia; quando o ajuste aparece depois de operações, os resultados anteriores são descontados para reconstruir a referência de abertura. Ajustes contábeis não são performance e não compõem o P&L. Não existe capital inicial informado manualmente.

As séries diárias usam `America/Bahia`, preenchem dias úteis sem encerramentos com retorno zero e excluem sábados e domingos; feriados não são modelados. Sharpe usa desvio-padrão amostral, taxa livre de risco zero e pelo menos duas observações. Sortino usa retorno mínimo aceitável zero e downside deviation sobre todas as observações, representando retornos não negativos por zero. A variante anualizada multiplica a razão diária por `sqrt(252)` e requer pelo menos 30 dias úteis.

O drawdown monetário é calculado sobre o P&L cumulativo das posições filtradas. O drawdown percentual usa um índice de performance iniciado em 100 e encadeado pelos retornos diários, evitando que ajustes contábeis sejam confundidos com performance. Os episódios de maior profundidade e maior duração são reportados separadamente, com pico, vale e recuperação; episódios não recuperados usam o fim da amostra como limite de duração e mantêm a recuperação nula. Valores percentuais não são limitados artificialmente a 100%.

## Consequências

- Resultados monetários permanecem disponíveis quando razões estatísticas não podem ser calculadas.
- Métricas temporais dependem da cobertura completa do saldo de abertura ajustado; cobertura incompleta produz `null` com motivo explícito.
- Os números monetários não recebem símbolo de moeda porque a fonte não declara formalmente a moeda da conta.
- Gráficos e curvas permanecem fora desta decisão e pertencem à Issue #7.
