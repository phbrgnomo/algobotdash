# Algobotdash

Dashboard local para transformar relatórios de trades automatizados em análises reproduzíveis por estratégia, símbolo e ciclo operacional.

## Language

**Fonte canônica**:
O arquivo Excel de histórico de trades fornecido manualmente. O SQLite é uma projeção derivada e pode ser reconstruído a partir dessa fonte.
_Avoid_: banco principal, dado definitivo (para o SQLite)

**Ciclo de trade**:
Unidade consolidada de uma operação lógica, incluindo entrada, piramidações e saída de uma estratégia.
_Avoid_: perna, ordem isolada (quando a análise é do resultado do ciclo)

**Ordem**:
Registro individual de execução associado a um ciclo, preservado como detalhe de rastreabilidade.
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
Resultado calculado sobre os ciclos normalizados e recalculado conforme o filtro solicitado.
_Avoid_: valor pré-calculado permanente

**Ciclo aberto**:
Ciclo que possui entrada sem saída correspondente no relatório; permanece rastreável, mas não entra nas métricas realizadas até ser encerrado.
_Avoid_: trade perdido

**Sem comentário**:
Registro preservado em um agregado separado para rastreabilidade, mas excluído das métricas por estratégia.
_Avoid_: sem estratégia

**Métrica por trade**:
Métrica calculada tratando cada ciclo realizado como uma observação, sem anualização implícita.
_Avoid_: métrica diária

**Importação**:
Execução identificada por hash da fonte, horário, contagens de linhas, ciclos, registros sem comentário e rejeições.
_Avoid_: carga sem rastreio

**Ambiguidade de agrupamento**:
Comentário que corresponde a mais de uma regra configurada; é erro de configuração e impede a conclusão da atualização.
_Avoid_: escolha por prioridade implícita

**Importação válida**:
Execução que conclui a reconstrução sem ambiguidades de agrupamento e mantém disponíveis os ciclos aceitos, os registros sem comentário e as rejeições.
_Avoid_: atualização parcial

**Execução principal**:
Modo Docker local que executa o backend FastAPI/Uvicorn e expõe o dashboard em localhost.
_Avoid_: instalação Python manual (como caminho padrão)

**Volumes locais**:
Diretórios do host montados no runtime para configuração (`config/`), projeção SQLite (`data/`) e exportações opcionais (`reports/`).
_Avoid_: dados embutidos na imagem
