# algobotdash

Dashboard local para processar o relatório padrão de histórico de trades do MetaTrader 5 e transformar operações automatizadas em análises reproduzíveis por estratégia, símbolo e posição.

> [!WARNING]
> Este repositório é público. Os arquivos reais de trades, planilhas, CSVs e relatórios gerados ficam fora do Git por design. Não publique dados de corretora neste repositório.

## Estado do projeto

O projeto está na fase de fundação e especificação. O parser legado já gera um relatório HTML a partir do histórico Excel; o dashboard dinâmico com FastAPI, SQLite e Docker Compose está dividido nas [issues do projeto](https://github.com/phbrgnomo/algobotdash/issues).

## Objetivo

O `algobotdash` será uma aplicação local que recebe o relatório padrão de trades exportado pelo MetaTrader 5 e:

- lê o relatório Excel padrão do MetaTrader 5 como fonte canônica;
- normaliza contratos futuros em famílias como `WIN`, `WDO` e `BIT`;
- agrupa comentários de estratégia e normaliza posições por símbolo;
- reconstrói uma projeção SQLite de forma idempotente;
- calcula métricas sob demanda;
- oferece filtros, tabelas e gráficos sincronizados;
- registra a origem e a qualidade de cada importação.

## Conceitos principais

- **Fonte canônica:** arquivo Excel atualizado manualmente.
- **Posição analítica:** unidade de resultado do MVP, preservada com suas ordens e transações de auditoria.
- **Ordem:** execução individual preservada como detalhe de auditoria.
- **Grupo de estratégia:** agrupamento configurável de comentários no YAML.
- **Símbolo normalizado:** família operacional independente do vencimento do contrato.
- **SQLite:** projeção derivada, reconstruível a partir da fonte.

Consulte o [glossário do projeto](CONTEXT.md) para a terminologia completa.

## Relatório legado

Enquanto o dashboard está sendo implementado, o relatório existente pode ser gerado diretamente a partir do workbook local.

### Importação para SQLite

O primeiro slice da Issue #2 reconstrói uma projeção SQLite a partir das seções `Posições`, `Ordens` e `Transações` do relatório. Prepare a configuração local e execute:

```bash
mkdir -p config
cp config.example.yaml config/config.yaml
cp .env.example .env
poetry install
poetry run python -m algobotdash
```

A atualização escreve uma base temporária e só a publica ao concluir. A projeção contém `imports`, `positions`, `orders`, `transactions` e `rejected_rows`; comentários sem grupo ficam preservados com `strategy` nula. Uma posição pode estar associada a uma ordem mesmo sem estratégia classificada, pois associação exige ticket e símbolo bruto coincidentes. A API mantém grupo e família do símbolo separados para auditoria e expõe a identidade analítica derivada `strategy_key` no formato `WIN FVG`, inclusive no catálogo observado `/api/strategy-keys`. `/api/filter-options` fornece os valores observados usados pelos filtros do dashboard. Uma ambiguidade de agrupamento interrompe a atualização e mantém a última projeção válida.

`/api/positions` usa `status=closed` e `association=all` por padrão. O filtro `association` aceita `associated`, `unassociated` e `all`; a resposta usa os dois primeiros valores para informar o vínculo de cada posição.

Até a entrega do MVP, o SQLite usa um único schema corrente, sem tabela de versão ou migrações. Depois de uma alteração estrutural, regenere a projeção a partir do Excel. O refresh reaproveita o histórico de importações somente quando a tabela `imports` anterior permanece legível.

### Pré-requisitos

- Python 3.10 ou superior;
- Poetry 1.8 ou superior.

### Execução

Coloque o workbook no caminho definido por `source.path` em `config/config.yaml` e execute:

```bash
poetry run python generate_trade_report.py
```

Os comandos Python do projeto devem ser executados com `poetry run`; o ambiente virtual local fica em `.venv/` e as versões resolvidas são registradas em `poetry.lock`.

Os artefatos são gravados em `reports/`. Essa pasta é ignorada pelo Git e não deve ser publicada quando contiver dados reais.

## Dashboard planejado

O runtime padrão será Docker Compose, com o dashboard disponível em `http://localhost:8765`.

Volumes locais planejados:

| Diretório | Finalidade |
|---|---|
| `config/` | YAML com caminho da fonte, regras de símbolos e grupos de estratégia |
| `source/` | Workbook Excel montado somente para leitura |
| `data/` | SQLite e estado derivado |
| `reports/` | Exportações locais opcionais |

A configuração YAML será a fonte canônica dos agrupamentos. O dashboard não editará o YAML no MVP.

### Runtime Docker

O container roda sem privilégios de root. Prepare os diretórios, a configuração, a fonte e o mapeamento para o usuário do host com:

```bash
mkdir -p config source data reports
cp config.example.yaml config/config.yaml
cp .env.example .env
cp /caminho/para/ReportHistory.xlsx source/ReportHistory.xlsx
sed -i "s/^ALGOBOTDASH_UID=.*/ALGOBOTDASH_UID=$(id -u)/" .env
sed -i "s/^ALGOBOTDASH_GID=.*/ALGOBOTDASH_GID=$(id -g)/" .env
docker compose up -d
```

Abra `http://localhost:8765/`. Para alterar a porta do host, edite `ALGOBOTDASH_PORT` no `.env` antes de iniciar o Compose.

O serviço não importa o workbook automaticamente. Para criar ou reconstruir a projeção no volume `data/`, execute:

```bash
docker compose run --rm algobotdash \
  python -m algobotdash
```

A execução direta via Poetry é o fallback de desenvolvimento:

```bash
poetry run uvicorn algobotdash.web:app --host 127.0.0.1 --port 8765
```

`.env.example` documenta as variáveis locais opcionais; copie-o para `.env`, que não é versionado, quando quiser personalizá-las. `ALGOBOTDASH_CONFIG` e `ALGOBOTDASH_DATABASE` usam caminhos relativos válidos tanto na raiz do projeto quanto em `/app`. O Compose usa valores padrão para ambos e consulta `.env` apenas para interpolar sua configuração; a CLI, o backend e `generate_trade_report.py` carregam o arquivo diretamente quando ele existe. Valores já definidos no processo têm precedência sobre `.env`, e argumentos `--config`/`--database` têm precedência final na importação.

`ALGOBOTDASH_UID` e `ALGOBOTDASH_GID` fazem o processo no container gravar `data/` e `reports/` como o usuário do host; ajuste ambos para evitar arquivos inacessíveis depois da importação.

O dashboard inicial é uma página própria de estado operacional. Ele não serve nem reutiliza o HTML legado produzido por `generate_trade_report.py`; `reports/` permanece reservado para exportações futuras.

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

Posições sem comentário ficam em um agregado separado. A reconstrução de ciclos de piramidação está fora do MVP.

## Desenvolvimento

O trabalho está organizado na [Issue #1](https://github.com/phbrgnomo/algobotdash/issues/1) e nos tickets dependentes:

1. [Importação e reconstrução de posições](https://github.com/phbrgnomo/algobotdash/issues/2)
2. [Runtime Docker e configuração local](https://github.com/phbrgnomo/algobotdash/issues/3)
3. [API de consulta e visão básica de posições](https://github.com/phbrgnomo/algobotdash/issues/4)
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
