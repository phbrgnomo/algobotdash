# Convenções de métricas sobre posições analíticas

## Status

Accepted

## Decisão

As métricas são calculadas sob demanda sobre posições analíticas finalizadas, sem reconstruir ciclos. Métricas gerais incluem posições sem estratégia comprovada para preservar a reconciliação do resultado total; métricas filtradas por estratégia consideram somente posições associadas. Posições abertas continuam visíveis, mas não compõem métricas realizadas.

P&L é a soma do resultado líquido informado nas posições. Taxa de acerto é a quantidade de posições com P&L positivo dividida por todas as posições realizadas, incluindo resultados iguais a zero no denominador. Profit factor é o ganho bruto dividido pelo módulo da perda bruta; payoff é o ganho médio dividido pelo módulo da perda média; expectância é a média aritmética do P&L líquido. Métricas sem denominador válido retornam `null` com motivo explícito, nunca infinito.

Sharpe e Sortino por posição usam o P&L monetário líquido, sem anualização. As variantes diárias usam o retorno diário da amostra filtrada: P&L das posições encerradas no dia dividido pelo saldo de abertura ajustado. Esse saldo é o capital contábil após o primeiro lançamento cujo comentário é exatamente `Ajuste de Saldo` e antes dos resultados operacionais do dia. Empates de instante usam o menor identificador numérico da transação, com ordem textual determinística para identificadores não numéricos. Quando o ajuste aparece depois de operações, P&L, comissão, taxa e swap das transações globais anteriores são descontados para reconstruir a referência de abertura. Ajustes posteriores não redefinem o denominador. Ajustes contábeis não são performance e não compõem o P&L. Não existe capital inicial informado manualmente.

As séries diárias usam `America/Bahia`, preenchem dias úteis sem encerramentos com retorno zero e excluem sábados e domingos; feriados não são modelados. Saldo positivo é obrigatório nos dias com posições filtradas encerradas, mas não nos dias preenchidos com zero. Sharpe usa desvio-padrão amostral, taxa livre de risco zero e pelo menos duas observações. Sortino usa retorno mínimo aceitável zero e downside deviation sobre todas as observações, representando retornos não negativos por zero. A variante anualizada multiplica a razão diária por `sqrt(252)` e requer pelo menos 30 dias úteis.

O drawdown monetário é calculado sobre o P&L cumulativo das posições filtradas. O drawdown percentual usa um índice de performance iniciado em 100 e encadeado pelos retornos diários, evitando que ajustes contábeis sejam confundidos com performance. Os episódios de maior profundidade e maior duração são reportados separadamente, com pico, vale e recuperação; episódios não recuperados usam o fim da amostra como limite de duração e mantêm a recuperação nula. Valores percentuais não são limitados artificialmente a 100%.

## Episódios monetários (Issue #16)

A curva começa em zero na meia-noite de `effective_date_from` em `America/Bahia`.
Os limites efetivos seguem a mesma resolução das métricas temporais, inclusive quando
os filtros de data são omitidos. Encerramentos no mesmo instante são agregados em UTC
antes de atualizar a curva. A soma usa a representação decimal dos valores monetários
de forma exata, evitando que resíduos binários impeçam uma recuperação; não há
arredondamento para centavos no cálculo.

Um episódio começa abaixo do pico e termina ao atingir ou superar esse pico.
Picos e vales iguais preservam a primeira ocorrência. `depth` é negativo, calculado
como vale menos pico. `duration_days` é a diferença entre datas civis em Bahia,
sem adicionar um dia: episódios intradiários podem durar zero dias. Para episódios
abertos, o limite é `effective_date_to`, mesmo sem operações nessa data.

`GET /api/metrics` publica `monetary_drawdown` com `state`, `deepest_episode` e
`longest_episode`. Cada episódio contém `depth`, `peak_at`, `valley_at`, `recovery_at`
e `duration_days`; timestamps são ISO 8601 UTC com sufixo `Z`. Empates entre episódios
selecionam o mais antigo; o mesmo episódio pode ocupar os dois campos. Os estados são
`available`, `no_drawdown`, `empty_sample` e `unavailable`; os últimos três mantêm
ambos os episódios nulos. Indisponibilidade usa `unavailable_reasons.monetary_drawdown`.
Cobertura de saldo ausente não impede drawdown monetário. Agregados que excedem a
representação numérica na leitura mantêm o erro de projeção existente; overflow
durante o cálculo do drawdown produz `unavailable`, sem publicar episódios parciais.

## Consequências

- Resultados monetários permanecem disponíveis quando razões estatísticas não podem ser calculadas.
- Métricas temporais dependem da cobertura completa do saldo de abertura ajustado; cobertura incompleta produz `null` com motivo explícito.
- Os números monetários não recebem símbolo de moeda porque a fonte não declara formalmente a moeda da conta.
- Gráficos e curvas permanecem fora desta decisão e pertencem à Issue #7.
