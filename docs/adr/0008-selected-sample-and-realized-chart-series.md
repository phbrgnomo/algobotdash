# Uma amostra selecionada para gráficos, cartões e tabela

As seleções múltiplas independentes de símbolo e estratégia definem uma única
amostra para toda a análise. O total destacado soma as combinações selecionadas,
incluindo uma categoria residual selecionável para posições sem estratégia
comprovada. Apenas esconder linhas na apresentação manteria métricas de uma
amostra diferente e impediria reconciliar o total com as curvas visíveis.

As séries de P&L realizado e acumulado são agregadas no backend sobre posições
finalizadas, com início em zero e períodos de calendário recortados pelo filtro.
As séries de drawdown reutilizam os geradores dos cartões, preservando a
frequência por encerramento no monetário e diária no percentual. Recalcular
essas trajetórias no navegador a partir da tabela paginada perderia operações
e poderia ocultar quedas intradiárias. As convenções da ADR 0003 continuam
válidas; detalhes do contrato estão na especificação da Issue #7.

As consultas independentes são fixadas à revisão publicada escolhida pelo
dashboard. Uma revisão diferente invalida a amostra inteira antes da recarga,
evitando combinar tabela, cartões e gráficos de importações distintas.
