# Reconstrução de ciclos orientada por execuções

## Status

Accepted

## Context

O relatório do MetaTrader 5 separa posições, ordens e transações. Uma posição é o resumo operacional usado no MVP, uma ordem pode ser parcialmente executada ou rejeitada e uma ordem pode resultar em múltiplas transações. A Issue #2 precisa preservar a auditoria sem inventar relações.

## Decision

As transações serão preservadas como evidência de execução. As ordens serão preservadas como registros de intenção/estado e as posições serão a unidade analítica do MVP. Nenhum ciclo será criado nesta etapa; registros sem vínculo explícito permanecerão não associados.

Volumes compostos serão separados em volume solicitado e volume executado. Ambos serão preservados; o resultado por posição usará o P&L informado em `Posições`.

Ordens rejeitadas ou canceladas serão preservadas sem gerar execução. O P&L por operação será o P&L informado em `Posições`; a soma das transações servirá para reconciliação global e auditoria. O histórico de importações será persistente, enquanto as tabelas derivadas poderão ser reconstruídas atomicamente.

Não será inferida associação de saída por horário, volume ou proximidade temporal no MVP. Candidatos ambíguos permanecerão não associados.

O desenho futuro poderá agrupar posições de uma entrada-base e suas piramidações como um único ciclo quando houver evidência suficiente. No MVP, essa associação não será feita: cada `Position` será a unidade analítica, com agregação por estratégia e símbolo. As posições, ordens e transações permanecerão como detalhes independentes; a reconstrução de ciclos fica para uma evolução posterior.

A configuração de estratégia continuará declarando padrões de comentários, mas não será usada para afirmar uma linhagem de ciclo no MVP.

No MVP, posições sem um vínculo explícito com uma ordem, ou com símbolo não normalizado, continuarão preservadas, mas ficarão fora das agregações normalizadas. O P&L por posição será obtido do resumo de posições; comentários e estratégias serão atribuídos às ordens e às transações que referenciam explicitamente essas ordens, sem projetar esse vínculo para posições.

## Consequences

- A projeção preserva a diferença entre ordem, execução, posição e ciclo.
- Piramidações podem ser auditadas sem tratar cada ordem como um ciclo independente.
- Dados sem vínculo não contaminam métricas por meio de agrupamentos heurísticos.
- O histórico de importações não é perdido quando a projeção derivada é substituída.
- A reconstrução precisa validar explicitamente quais identificadores o relatório fornece para estabelecer o vínculo.

## Alternatives considered

- Usar somente a seção `Posições`: simples, mas perde ordens e execuções individuais.
- Agrupar por proximidade temporal, estratégia e símbolo: pode juntar operações distintas e não comprova a relação.
- Criar ciclos provisórios para todo registro sem vínculo: preserva linhas, mas fabrica uma unidade analítica sem evidência.
