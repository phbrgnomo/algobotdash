# Gráficos e análise visual

Referência: [Issue #7](https://github.com/phbrgnomo/algobotdash/issues/7).

Status: decisões fechadas via `grill-with-docs`; implementação local autorizada.

## Decisões confirmadas

- A curva principal representa P&L líquido realizado acumulado, com início em zero
  na amostra filtrada. O nome público é “P&L acumulado”. Não representa saldo
  contábil nem patrimônio marcado a mercado.
- O filtro de datas define o intervalo da amostra.
- Um seletor permite agregar o resultado por dia, semana, mês ou ano.
- Os períodos seguem o calendário no fuso analítico, com semana de segunda a
  domingo. Os períodos nas extremidades são recortados pelo filtro de datas e
  identificados como parciais quando necessário.
- As barras representam o resultado total de cada período. A linha acumulada
  acompanha essa agregação, sem reiniciar entre períodos.
- Cada série analítica corresponde à combinação de símbolo normalizado e
  estratégia, preservando a identidade analítica já definida no glossário.
- Símbolo e estratégia são seletores múltiplos independentes. A seleção admite
  qualquer símbolo escolhido combinado com qualquer estratégia escolhida,
  desde que essa combinação exista; não representa uma lista de pares avulsos.
- A categoria selecionável “Sem estratégia comprovada”, separada por símbolo,
  preserva o resultado não classificado na decomposição do total. Está incluída
  na seleção inicial junto às estratégias.
- Inicialmente, o total aparece em destaque e as estratégias aparecem
  individualmente com menor destaque. As combinações possuem cores distintas
  e consistentes.
- O total destacado é a soma das combinações selecionadas, não uma referência
  fixa à carteira inteira.
- A consulta por mouse ou toque apresenta o total selecionado em destaque e
  também os resultados individuais.
- O drawdown monetário conserva a frequência por instante de encerramento.
  O percentual conserva o encadeamento de retornos diários. A agregação das
  barras não altera essas convenções.
- Uma seleção vazia mostra “Nenhuma combinação selecionada”, sem curvas ou
  métricas apresentadas. Omitir um filtro na API continua significando todos.
- Apenas posições finalizadas produzem observações. São publicados os períodos
  com encerramentos na amostra, inclusive quando seu resultado líquido é zero.
  Uma estratégia sem encerramento em um período observado nas demais mantém
  seu acumulado constante. Uma amostra inteiramente vazia tem estado explícito.
- Alterações de datas, direção ou status preservam as seleções. Combinações sem
  resultados no período continuam selecionadas.
- Após atualizar a fonte, escolhas específicas são preservadas. Novas opções
  entram automaticamente apenas no modo “Todos”. Opções escolhidas que
  desapareceram são mantidas com indicação de indisponibilidade.

## Restrições existentes

- Gráficos, cartões e tabela devem descrever a mesma amostra filtrada.
- Posições analíticas são a unidade de análise; ciclos não são reconstruídos.
- Ajustes de saldo não compõem P&L operacional.
- As datas são interpretadas no fuso analítico configurado.
- Posições abertas não entram no resultado realizado.
- A moeda não deve ser presumida na apresentação dos valores.

## Contrato e implementação

- `GET /api/series` usa os mesmos filtros de posições e métricas, sem paginação,
  e aceita `granularity=day|week|month|year`.
- `strategy` e `symbol_family` admitem parâmetros repetidos: OR dentro de cada
  dimensão, AND entre dimensões. Valores únicos continuam compatíveis.
- `include_unclassified=true` acrescenta a categoria residual às estratégias
  informadas; sem `strategy`, seleciona somente a categoria residual.
  `selection_empty=true` representa explicitamente nenhuma combinação.
- A categoria residual contém posições com estratégia nula ou sem associação
  comprovada, mesmo que exista texto de estratégia na posição.
- Símbolos sem família normalizada usam o símbolo bruto no seletor e na série,
  preservando a decomposição aditiva do total.
- Séries e insumos de drawdown são lidos no mesmo snapshot da projeção. O
  payload informa sua revisão, fuso, limites efetivos, períodos e séries por
  combinação. Drawdowns usam os mesmos geradores exatos dos cartões.
- Estados públicos são `available`, `empty_selection`, `empty_sample` e
  `unavailable`. Falta de saldo torna somente o percentual indisponível.
- O frontend usa SVG local, sem dependência externa ou CDN. Mouse, toque e
  teclado consultam o total e os resultados individuais. A renderização se
  ajusta à largura e descarta respostas de séries de gerações anteriores.
- A ordenação e a paginação afetam somente a tabela. A agregação de período
  afeta somente o gráfico de performance, preservando os drawdowns originais.
- As três consultas compartilham `projection_revision`, obtida do estado do
  serviço. Cada leitor verifica a revisão na própria conexão SQLite; uma
  publicação concorrente produz HTTP 409 `projection_revision_changed`. O
  dashboard invalida toda a amostra e consulta novamente o estado antes de
  carregar a nova revisão, inclusive para paginação e mudança de agregação.
- Cada dimensão é ligada ao SQL como um único array JSON via `json_each`,
  preservando as 500 opções por dimensão sem exceder 999 variáveis combinadas.
  O runtime SQLite deve oferecer JSON1, como nas imagens Python adotadas.
- Somatórios monetários dos cartões, séries e P&L diário usam a representação
  decimal exata dos valores projetados, com uma conversão final para número
  JSON. Não há arredondamento intermediário; `0.1 + 0.2 - 0.3` resulta em zero.
- A leitura, agregação e serialização das séries executam fora do loop HTTP.
  Por projeção e processo, há no máximo duas consultas em execução e 30 inícios
  por minuto. Rejeições imediatas usam HTTP 429 `series_rate_limit` e
  `Retry-After`; uma desconexão não libera o limite antes de o worker terminar.
- Séries aceitam até 50.000 posições lidas, 50.000 transações dos dias relevantes
  e 50.000 pontos na matriz estratégia/período. Consultas SQLite têm orçamento
  de cinco segundos. Excesso retorna HTTP 413 `series_too_large`; o operador
  deve reduzir o intervalo ou a seleção. Nenhuma série parcial é publicada.
- O navegador cancela a consulta de séries substituída e limita a consulta e a
  leitura do corpo a 15 segundos. Timeout limpa o gráfico e permite nova
  tentativa pelo ciclo de estado existente; erros 422 preservam sua explicação.

## Verificação

- Testes de API conferem soma das curvas versus total e métricas, multisseleção,
  categoria residual, períodos parciais, calendário, fuso, amostras vazias e
  abertas, recuperação decimal, overflow e reconciliação de drawdowns.
- O smoke real em `tests/browser_smoke.py` usa fixtures descartáveis e cobre
  desktop/mobile, mouse/toque/teclado, filtros compartilhados, agregações,
  respostas atrasadas e reconstrução da fonte via atualização assíncrona.
