# Algobotdash

Dashboard local para transformar relatórios de trades automatizados em análises reproduzíveis por estratégia, símbolo e posição analítica.

## Language

**Fonte canônica**:
O arquivo Excel de histórico de trades fornecido manualmente. O SQLite é uma projeção derivada e pode ser reconstruído a partir dessa fonte.
_Avoid_: banco principal, dado definitivo (para o SQLite)

**Ciclo de trade**:
Unidade consolidada de uma operação lógica, incluindo entrada, piramidações e saída de uma estratégia.
_Avoid_: perna, ordem isolada (quando a análise é do resultado do ciclo)

Um ciclo é formado somente quando a relação entre suas execuções pode ser comprovada pelo relatório. Entradas, piramidações e saída permanecem como eventos individuais de execução dentro do ciclo.

**Execução**:
Evento efetivamente realizado no mercado, preservado com seu horário, preço, direção e volume executado.
_Avoid_: ordem (uma ordem pode ser parcialmente executada ou rejeitada)

**Volume executado**:
Quantidade efetivamente realizada em uma execução; é preservada para auditoria e reconciliação.
_Avoid_: volume solicitado, volume exibido sem distinguir execução

**Volume solicitado**:
Quantidade originalmente submetida em uma ordem, preservada separadamente quando o relatório também informa a quantidade executada.
_Avoid_: volume do ciclo

**Registro não associado**:
Ordem ou execução preservada para auditoria quando não existe vínculo comprovável com uma posição.
_Avoid_: ciclo provisório, rejeição automática

Ordens rejeitadas ou canceladas permanecem como ordens preservadas, mas não produzem execução, ciclo ou métrica. Uma ordem parcialmente executada preserva a quantidade solicitada e a quantidade executada separadamente.

**P&L do ciclo**:
Resultado consolidado pela soma do P&L das execuções associadas ao ciclo, considerando os custos registrados nas próprias execuções.
_Avoid_: lucro copiado sem reconciliação da posição

**Histórico de importações**:
Registro permanente das atualizações válidas, incluindo a identificação da fonte, seu hash, horário e indicadores de qualidade.
_Avoid_: parte descartável da projeção

**Confiança do vínculo**:
Grau de evidência de que uma ordem ou execução pertence a um ciclo, determinado por sinais concordantes do relatório; vínculos ambíguos permanecem não associados.
_Avoid_: escolha arbitrária, certeza inferida

**Ciclo de piramidação**:
Ciclo único composto por uma entrada-base e uma ou mais entradas adicionais da mesma operação, reconhecidas por linhagem de estratégia, ativo, direção e encerramento comum; cada posição e execução continua auditável separadamente.
_Avoid_: soma cega de posições, ordem isolada como ciclo

No MVP do algobotdash, ciclos de piramidação não são reconstruídos. A `Position` é a unidade analítica; a reconstrução de ciclos é uma evolução posterior que exigirá vínculo explícito ou regras próprias.

**Agregação por estratégia**:
Visão que soma posições classificadas pela configuração de comentários e símbolos, sem afirmar que posições relacionadas formam um ciclo único.
_Avoid_: ciclo implícito, vínculo de saída inferido

**Identidade analítica da estratégia**:
Chave derivada pela combinação do símbolo normalizado e do grupo de comentário, como `WIN FVG`. O grupo e a família permanecem disponíveis separadamente para auditoria e filtros.
_Avoid_: agrupar somente por comentário, misturar a mesma regra entre famílias de símbolos

**Reconciliação global**:
Conferência entre o P&L consolidado das posições e a soma das transações do relatório, sem usar essa conferência para inventar vínculos por estratégia.
_Avoid_: atribuição de saída por igualdade de P&L

**Posição analítica**:
Unidade de análise do MVP, identificada pelo registro de posição; é classificada pela ordem correspondente quando `position_id` e `symbol_raw` coincidem e a ordem possui uma estratégia. Ciclos de piramidação não são inferidos nesta versão.
_Avoid_: ciclo implícito, agrupamento por proximidade

**Ordem**:
Registro individual de intenção/estado do relatório, preservado como detalhe de rastreabilidade da posição quando houver vínculo explícito.
_Avoid_: trade completo

**Estratégia**:
Regra operacional identificada pelo comentário do relatório e normalizada por configuração.
_Avoid_: robô (quando se refere à identidade estatística analisada)

**Grupo de estratégia**:
Agrupamento definido no arquivo YAML de configuração para comentários que representam uma única estratégia lógica, incluindo suas piramidações.
_Avoid_: perna de estratégia

**Configuração de agrupamento**:
Arquivo YAML local que define como comentários e símbolos são normalizados. É a fonte canônica dessa regra no MVP.
_Avoid_: configuração editada pelo dashboard

**Símbolo normalizado**:
Contrato agrupado na família operacional relevante, como `WIN`, `WDO` ou `BIT`, independentemente do vencimento.
_Avoid_: ticker bruto (na análise comparativa)

**Sem estratégia**:
Categoria explícita para registros cujo comentário não corresponde a nenhum agrupamento configurado.
_Avoid_: ignorado, descartado

**Atualização**:
Processo explícito que lê a fonte canônica, reconstrói a projeção SQLite e informa o resultado da operação sem invalidar a última projeção válida em caso de erro.
_Avoid_: sincronização silenciosa

**Métrica**:
Resultado calculado sobre as posições analíticas normalizadas e recalculado conforme o filtro solicitado.
_Avoid_: valor pré-calculado permanente

**Posição aberta**:
Posição que possui entrada sem saída correspondente no relatório; permanece rastreável, mas não entra nas métricas realizadas além do P&L explicitamente informado pela fonte.
_Avoid_: trade perdido

**Sem comentário**:
Registro preservado em um agregado separado para rastreabilidade, mas excluído das métricas por estratégia.
_Avoid_: sem estratégia

**Métrica por trade**:
Métrica calculada tratando cada posição realizada como uma observação, sem anualização implícita.
_Avoid_: métrica diária

**Importação**:
Execução identificada por hash da fonte, horário, contagens de linhas, posições, registros sem comentário e rejeições.
_Avoid_: carga sem rastreio

**Ambiguidade de agrupamento**:
Comentário que corresponde a mais de uma regra configurada; é erro de configuração e impede a conclusão da atualização.
_Avoid_: escolha por prioridade implícita

**Importação válida**:
Execução que conclui a reconstrução sem ambiguidades de agrupamento e mantém disponíveis as posições, os registros sem comentário e as rejeições.
_Avoid_: atualização parcial

**Execução principal**:
Modo Docker local que executa o backend FastAPI/Uvicorn e expõe o dashboard em localhost.
_Avoid_: instalação Python manual (como caminho padrão)

**Volumes locais**:
Diretórios do host montados no runtime para configuração (`config/`), projeção SQLite (`data/`) e exportações opcionais (`reports/`).
_Avoid_: dados embutidos na imagem
