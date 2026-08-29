# Excel como fonte canônica e SQLite como projeção derivada

O dashboard usará o relatório Excel atualizado manualmente como fonte canônica e reconstruirá o SQLite como uma projeção derivada durante uma atualização explícita. Essa separação preserva a rastreabilidade do arquivo original, permite reprocessamento idempotente e evita que métricas calculadas sejam confundidas com dados de origem.
