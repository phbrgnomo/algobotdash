# algobotdash

Dashboard local para processar o relatório padrão de histórico de trades do MetaTrader 5 e transformar operações automatizadas em análises reproduzíveis por estratégia, símbolo e ciclo operacional.

> [!WARNING]
> Este repositório é público. Os arquivos reais de trades, planilhas, CSVs e relatórios gerados ficam fora do Git por design. Não publique dados de corretora neste repositório.

## Estado do projeto

O projeto está na fase de fundação e especificação. O parser legado já gera um relatório HTML a partir do histórico Excel; o dashboard dinâmico com FastAPI, SQLite e Docker Compose está dividido nas [issues do projeto](https://github.com/phbrgnomo/algobotdash/issues).

## Objetivo

O `algobotdash` será uma aplicação local que recebe o relatório padrão de trades exportado pelo MetaTrader 5 e:

- lê o relatório Excel padrão do MetaTrader 5 como fonte canônica;
- normaliza contratos futuros em famílias como `WIN`, `WDO` e `BIT`;
- agrupa comentários de estratégia, incluindo piramidações, em ciclos completos;
- reconstrói uma projeção SQLite de forma idempotente;
- calcula métricas sob demanda;
- oferece filtros, tabelas e gráficos sincronizados;
- registra a origem e a qualidade de cada importação.

## Conceitos principais

- **Fonte canônica:** arquivo Excel atualizado manualmente.
- **Ciclo de trade:** entrada, piramidações e saída de uma operação lógica.
- **Ordem:** execução individual preservada como detalhe de auditoria.
- **Grupo de estratégia:** agrupamento configurável de comentários no YAML.
- **Símbolo normalizado:** família operacional independente do vencimento do contrato.
- **SQLite:** projeção derivada, reconstruível a partir da fonte.

Consulte o [glossário do projeto](CONTEXT.md) para a terminologia completa.

## Relatório legado

Enquanto o dashboard está sendo implementado, o relatório existente pode ser gerado diretamente a partir do workbook local.

### Pré-requisitos

- Python 3.10 ou superior;
- `openpyxl`.

### Execução

Coloque o arquivo `ReportHistory-2002705608.xlsx` na raiz do projeto e execute:

```bash
python -m pip install openpyxl
python generate_trade_report.py
```

Os artefatos são gravados em `reports/`. Essa pasta é ignorada pelo Git e não deve ser publicada quando contiver dados reais.

## Dashboard planejado

O runtime padrão será Docker Compose, com o dashboard disponível em `http://localhost:8765`.

Volumes locais planejados:

| Diretório | Finalidade |
|---|---|
| `config/` | YAML com caminho da fonte, regras de símbolos e grupos de estratégia |
| `data/` | SQLite e estado derivado |
| `reports/` | Exportações locais opcionais |

A configuração YAML será a fonte canônica dos agrupamentos. O dashboard não editará o YAML no MVP.

## Fluxo de atualização planejado

1. Atualização manual do workbook.
2. Acionamento via CLI ou dashboard.
3. Validação do YAML e da fonte.
4. Reconstrução em uma projeção temporária.
5. Publicação atômica somente se a importação for válida.
6. Registro de hash, horário, contagens e rejeições.
7. Consulta da nova projeção pela API e pelo dashboard.

Em caso de falha, a última projeção válida permanece disponível.

## Métricas

O dashboard terá métricas de carteira e por estratégia, com filtros compartilhados:

- operações e P&L;
- profit factor, taxa de acerto, payoff e expectativa;
- profundidade, duração e recuperação de drawdown;
- Sharpe e Sortino por trade;
- versões diária e anualizada de Sharpe e Sortino;
- curvas de capital por carteira e estratégia.

Ciclos abertos aparecem para rastreabilidade, mas não entram nas métricas realizadas. Registros sem comentário ficam em um agregado separado.

## Desenvolvimento

O trabalho está organizado na [Issue #1](https://github.com/phbrgnomo/algobotdash/issues/1) e nos tickets dependentes:

1. [Importação e reconstrução de ciclos](https://github.com/phbrgnomo/algobotdash/issues/2)
2. [Runtime Docker e configuração local](https://github.com/phbrgnomo/algobotdash/issues/3)
3. [API de consulta e visão básica de ciclos](https://github.com/phbrgnomo/algobotdash/issues/4)
4. [Métricas sob demanda e filtros](https://github.com/phbrgnomo/algobotdash/issues/5)
5. [Atualização assíncrona e fallback](https://github.com/phbrgnomo/algobotdash/issues/6)
6. [Gráficos e análise visual](https://github.com/phbrgnomo/algobotdash/issues/7)
7. [Verificação operacional e publicação](https://github.com/phbrgnomo/algobotdash/issues/8)

As decisões de domínio estão em [`CONTEXT.md`](CONTEXT.md) e os registros arquiteturais em [`docs/adr/`](docs/adr/).

## Estrutura atual

```text
algobotdash/
├── generate_trade_report.py   # relatório legado
├── CONTEXT.md                 # glossário e limites do domínio
├── docs/adr/                  # decisões arquiteturais
├── docs/specs/                # especificação do dashboard
└── reports/                   # saída local ignorada pelo Git
```

## Segurança dos dados

O `.gitignore` exclui planilhas, CSVs, bancos locais, ambientes, logs e relatórios gerados. Antes de abrir um pull request, confirme:

- que nenhum arquivo de dados foi adicionado com `git status --ignored`;
- que não há tokens ou credenciais na alteração;
- que relatórios exportados não contêm dados reais;
- que caminhos pessoais não foram codificados no código ou na documentação.
