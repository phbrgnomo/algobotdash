# Atualização assíncrona observável e exclusão entre processos

## Status

Accepted

## Decisão

O backend executa atualizações solicitadas por `POST /api/refresh` fora do ciclo da
requisição. Cada tentativa recebe um identificador e registra estado, etapa e resultado
em um SQLite separado, ao lado da projeção. Esse registro não é substituído quando uma
nova projeção é publicada e não altera o histórico de importações válidas.

Um bloqueio de arquivo por caminho absoluto da projeção coordena a API e a CLI entre
processos. Uma segunda tentativa não espera em fila: a API retorna conflito e a CLI
termina com erro. Consultas continuam lendo a projeção publicada enquanto outra é
construída em arquivo temporário.

Antes de publicar, a operação define a revisão esperada e a grava na nova projeção. Após
um reinício, o backend marca uma tentativa interrompida como concluída quando encontra
essa revisão publicada. Sem essa evidência, registra erro de interrupção. Não existe
retomada automática.

O progresso representa etapas reais, sem percentual estimado. A última tentativa aparece
em `/api/status`; qualquer tentativa persistida pode ser consultada pelo identificador.
Falhas mantêm a projeção anterior, mas não mudam as regras existentes: YAML inválido ou
fuso incompatível continuam bloqueando consultas analíticas.

## Consequências

- Fechar ou recarregar o navegador não cancela a atualização.
- O diretório `data/` contém a projeção, o banco de operações e o arquivo de bloqueio.
- A solução pressupõe o runtime Linux/Docker adotado pelo projeto.
- Cancelamento, percentual e uma tela de histórico ficam fora desta entrega.
