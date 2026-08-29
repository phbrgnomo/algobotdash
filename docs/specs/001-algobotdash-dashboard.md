# Algobotdash: Dashboard Local de Trades Automatizados

## Problem Statement

O histórico de trades é atualizado manualmente em Excel e hoje depende de scripts e relatórios estáticos. Isso dificulta reconstruir os dados de forma idempotente, rastrear a origem das métricas, agrupar corretamente ciclos com piramidação e comparar estratégias por filtros temporais e operacionais.

## Solution

Construir um dashboard local executado preferencialmente via Docker Compose, com FastAPI/Uvicorn no backend e JavaScript sem framework no frontend. O Excel permanece como fonte canônica; cada atualização válida reconstrói uma projeção SQLite e registra metadados da importação. O dashboard consulta ciclos consolidados, ordens de detalhe e métricas calculadas sob demanda.

## User Stories

1. As the operator, I want to configure the Excel source path in YAML, so that the application does not depend on a hard-coded personal path.
2. As the operator, I want to configure symbol normalization in YAML, so that contract expirations are analyzed under their operational families.
3. As the operator, I want to define strategy groups and comment patterns in YAML, so that piramided orders can be treated as one logical strategy.
4. As the operator, I want ambiguous comment matches to stop the refresh with a clear warning, so that metrics are never produced from silent classification errors.
5. As the operator, I want to trigger a full rebuild from the CLI, so that the derived database can be recreated after correcting the source workbook.
6. As the operator, I want to trigger a full rebuild from the dashboard, so that routine updates do not require a terminal.
7. As the operator, I want refreshes to run asynchronously, so that the dashboard remains responsive.
8. As the operator, I want a second refresh to be rejected while one is running, so that concurrent rebuilds cannot corrupt the projection.
9. As the operator, I want the last valid projection to remain available after a failed refresh, so that an input error does not take down the dashboard.
10. As the operator, I want each import to record the source hash and execution timestamp, so that every result is traceable to an exact workbook.
11. As the operator, I want each import to record rows read, cycles created, records without comments and rejections, so that data quality is visible.
12. As the operator, I want cycles with an entry but no exit to remain visible as open, so that incomplete history is not lost.
13. As the operator, I want open cycles excluded from realized P&L and drawdown, so that unrealized results do not contaminate realized metrics.
14. As the operator, I want records without comments preserved in a separate aggregate, so that they remain auditable without being assigned to a strategy.
15. As the operator, I want malformed records preserved as rejections with reasons, so that one bad row does not hide the rest of the import.
16. As the operator, I want each cycle to consolidate its entry, pyramiding and exit orders, so that performance is measured on the logical trade.
17. As the operator, I want individual orders retained as cycle details, so that I can audit how a cycle was executed.
18. As the operator, I want trades grouped by normalized strategy and symbol family, so that contract expirations do not split one operational series.
19. As the operator, I want to query realized cycles through the API, so that the frontend and future tools use one contract.
20. As the operator, I want to filter by strategy, symbol, direction, status and date range, so that I can isolate a meaningful sample.
21. As the operator, I want table and chart filters to share one state, so that every visualization describes the same sample.
22. As the operator, I want sortable cycle tables, so that I can rank outcomes by date, P&L and risk.
23. As the operator, I want portfolio and per-strategy P&L, so that I can distinguish aggregate performance from individual edge.
24. As the operator, I want profit factor, win rate, payoff and expectancy, so that I can evaluate return quality.
25. As the operator, I want drawdown depth, duration and recovery time, so that I can evaluate capital risk.
26. As the operator, I want Sharpe and Sortino per trade by default, so that the primary convention is explicit and reproducible.
27. As the operator, I want separate daily and annualized Sharpe and Sortino views, so that time aggregation is not confused with per-trade metrics.
28. As the operator, I want equity curves for the portfolio and each strategy, so that concentration and contribution are visible.
29. As the operator, I want import status and errors shown in the dashboard, so that I know whether the current projection is trustworthy.
30. As the operator, I want the application to run on localhost through Docker Compose, so that setup is reproducible.
31. As the operator, I want host-mounted configuration and data volumes, so that source files and the SQLite projection survive container recreation.
32. As the operator, I want a public example configuration without real data, so that the repository documents the contract safely.
33. As the maintainer, I want deterministic parser and grouping tests, so that changes cannot silently alter historical classification.
34. As the maintainer, I want API integration tests for refresh state and fallback behavior, so that external behavior is protected.
35. As the maintainer, I want a browser smoke test for loading, filtering and chart refresh, so that the dashboard remains usable without coupling tests to DOM implementation details.

## Implementation Decisions

- Excel is the canonical source; SQLite is a disposable, derived projection.
- Every refresh builds a temporary valid projection and publishes it atomically only after validation.
- Import metadata is retained separately, including source hash, timestamps and quality counts.
- YAML is canonical for source path, symbol normalization and strategy grouping; the dashboard reads but does not edit it in the MVP.
- Strategy grouping supports one logical group containing base and pyramiding comment patterns.
- A comment matching multiple groups is a configuration error and blocks completion with an explicit warning.
- Symbols are normalized by configured prefix rules, including WIN, WDO and BIT contract families.
- A cycle is the primary analytical unit; individual orders remain child details for auditability.
- Open cycles are displayed but excluded from realized metrics.
- Records without comments are retained in a separate aggregate and excluded from per-strategy metrics.
- Other invalid records are retained as rejected rows with a reason and excluded from metrics.
- Runtime is Docker Compose first, with FastAPI/Uvicorn in the container and Python direct execution as a development fallback.
- Host volumes are used for YAML configuration and SQLite data; report exports are optional.
- Default local port is 8765 and remains configurable.
- The API is read-oriented for queries, with a separate asynchronous refresh operation.
- Initial API resources include cycles, orders, metrics, strategies, import history and refresh status.
- Filters use one shared query state across tables and charts.
- Metrics are calculated on demand. Per-trade Sharpe and Sortino are the default; daily and annualized variants are explicit alternatives.
- The primary refresh service is the highest test seam. API integration tests and one thin browser smoke test cover external behavior around it.

## Testing Decisions

- Tests assert externally observable behavior and persisted results, not private helper calls or DOM structure.
- Fixtures cover valid rows, contract prefixes, missing comments, malformed values and open cycles.
- Grouping tests cover base-plus-pyramiding groups, unmatched comments and ambiguous matches that block refresh.
- Reconstruction tests verify idempotence, atomic replacement, import metadata and preservation of the last valid projection after failure.
- Cycle tests verify consolidation of entry, pyramiding and exit orders and exclusion of open cycles from realized metrics.
- Metric tests verify P&L, profit factor, win rate, payoff, drawdown, expectancy, Sharpe and Sortino under declared conventions.
- API tests verify filtering, shared query semantics, asynchronous refresh state, rejection of concurrent refresh and failure fallback.
- Browser smoke tests verify page load, synchronized filter effects and visible refresh status.
- No existing application test suite provides prior art; the first tests establish the service and API seams described above.

## Out of Scope

- Cloud hosting, multi-user access or authentication.
- Automatic scheduled imports.
- Editing YAML grouping rules from the dashboard.
- Replacing the Excel source with a broker API.
- Live order execution, broker connectivity or trading decisions.
- Imputing prices for open or incomplete cycles.
- Publishing real workbooks, CSV exports or generated reports in the public repository.
- Reproducing every legacy report layout as a dashboard requirement.
- Advanced portfolio optimization or automatic Kelly sizing in the MVP.

## Further Notes

- The repository is public; real data and generated reports remain ignored and local.
- A future YAML editor may be added only if it writes the same canonical configuration and preserves reviewable changes.
- The dashboard must expose source filename, source hash and last successful import so displayed metrics can be reconciled with the local workbook.
