"""Tests for dashboard endpoints and health diagnostics."""
# pylint: disable=too-many-lines

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import subprocess  # nosec B404 -- required to execute the fixed Node.js test harness
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from algobotdash.storage import SCHEMA
from algobotdash.refresh import RefreshOperation, RefreshOperationStore
from algobotdash.service import ImportSummary
from algobotdash.web import app, dashboard, health
from tests.fixture_helpers import workbook

NODE_EXECUTABLE = shutil.which("node")


class WebTests(unittest.TestCase):  # pylint: disable=too-many-public-methods
    """Verify the dashboard's HTTP-facing behavior."""
    tmp_path: Path = Path()
    config_dir: Path = Path()
    source_dir: Path = Path()
    data_dir: Path = Path()

    def setUp(self) -> None:
        """Create isolated config, source, and database paths."""
        self.tmp_path = Path(tempfile.mkdtemp(prefix="algobotdash-web-tests-"))
        self.config_dir = self.tmp_path / "config"
        self.source_dir = self.tmp_path / "source"
        self.data_dir = self.tmp_path / "data"
        self.config_dir.mkdir()
        self.source_dir.mkdir()
        self.data_dir.mkdir()

    def tearDown(self) -> None:
        """Remove the isolated test workspace."""
        shutil.rmtree(self.tmp_path)

    def _paths(self):
        """Patch web paths to point to the isolated workspace."""
        return patch.multiple(
            "algobotdash.web",
            CONFIG_PATH=self.config_dir / "config.yaml",
            DATABASE_PATH=self.data_dir / "algobotdash.sqlite",
        )

    def _write_config(self) -> None:
        """Write a valid configuration for the fixture source."""
        (self.config_dir / "config.yaml").write_text(
            f"timezone: America/Bahia\nsource:\n  path: {self.source_dir / 'ReportHistory.xlsx'}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _request(
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Issue one request through the public ASGI interface."""
        request_headers = headers
        if request_headers is None and method == "POST" and path == "/api/refresh":
            request_headers = {
                "X-Algobotdash-Request": "refresh",
                "Origin": "http://localhost",
            }

        async def request() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.request(method, path, headers=request_headers)

        return asyncio.run(request())

    def _wait_for_refresh(self, operation_id: str) -> dict[str, Any]:
        """Poll a refresh operation until it reaches a terminal public state."""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self._request("GET", f"/api/refresh/{operation_id}")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            if payload["state"] in {"completed", "error"}:
                return payload
            time.sleep(0.01)
        self.fail("refresh operation did not finish")

    def _health_payload(self) -> dict[str, Any]:
        """Read health state using the isolated test paths."""
        with self._paths():
            return health()

    def _health_payload_with(self, **expected: str) -> dict[str, Any]:
        """Read health state and check the named fields shared by a fixture."""
        payload = self._health_payload()
        self.assertEqual({field: payload[field] for field in expected}, expected)
        return payload

    def _open_valid_projection(self) -> sqlite3.Connection:
        """Create a current-schema projection after preparing valid configuration."""
        self._write_config()
        connection = sqlite3.connect(self.data_dir / "algobotdash.sqlite")
        connection.executescript(SCHEMA)
        return connection

    def _commit_projection_and_read_health(
        self, connection: sqlite3.Connection,
    ) -> dict[str, Any]:
        """Publish a fixture projection before reading its public health state."""
        connection.commit()
        connection.close()
        return self._health_payload()

    def test_dashboard_serves_own_static_page(self) -> None:
        """Dashboard should serve its own static HTML page."""
        response = dashboard()

        self.assertEqual(response.media_type, "text/html")
        response_path = Path(response.path)
        self.assertEqual(response_path.name, "index.html")
        page = response_path.read_text(encoding="utf-8")
        self.assertIn("Dashboard local", page)
        self.assertNotIn("generate_trade_report", page)

    def test_dashboard_static_contract_includes_status_and_positions_view(self) -> None:
        """Dashboard should retain the query API calls and basic table controls."""
        page = dashboard().path
        content = Path(page).read_text(encoding="utf-8")

        self.assertIn('fetch("/api/status"', content)
        self.assertIn('fetch("/api/refresh"', content)
        self.assertIn('id="refresh-button"', content)
        self.assertIn('id="refresh-state" aria-live="polite"', content)
        self.assertIn('fetch("/api/filter-options"', content)
        self.assertIn("projection_revision", content)
        self.assertIn("timeZone: analysisTimezone", content)
        self.assertIn("/api/positions?${query}", content)
        self.assertIn("/api/metrics?${query}", content)
        self.assertIn('id="filter-strategy"', content)
        self.assertIn('id="filter-symbol-family"', content)
        self.assertIn('id="filter-direction"', content)
        self.assertIn('id="filter-status"', content)
        self.assertIn('id="filter-association"', content)
        self.assertIn('id="date-from"', content)
        self.assertIn('id="date-to"', content)
        self.assertIn('id="positions-body"', content)
        self.assertIn('id="previous-page"', content)
        self.assertIn('id="next-page"', content)
        self.assertIn('<fieldset class="filters">', content)
        self.assertIn('<legend>Filtros das posições</legend>', content)
        self.assertIn('id="filter-state" aria-live="polite"', content)
        self.assertIn('id="table-state" role="alert"', content)
        self.assertIn('id="metrics-state" aria-live="polite"', content)
        self.assertIn('id="metric-sample-size"', content)
        self.assertIn('id="metric-winning-trades"', content)
        self.assertIn('id="metric-losing-trades"', content)
        self.assertIn('id="metric-net-pnl"', content)
        self.assertIn('id="metric-sortino-per-position"', content)
        self.assertIn('id="daily-metrics-title"', content)
        self.assertIn('id="metric-sharpe-daily"', content)
        self.assertIn('id="metric-sortino-daily"', content)
        self.assertIn('id="annualized-metrics-title"', content)
        self.assertIn('id="metric-sharpe-annualized"', content)
        self.assertIn('id="metric-sortino-annualized"', content)
        self.assertIn('id="temporal-metrics-summary"', content)
        self.assertIn("Projeção indisponível.", content)

    @unittest.skipUnless(NODE_EXECUTABLE, "requires Node.js for JavaScript execution")
    def test_dashboard_invalidates_pending_queries_and_validates_filters(self) -> None:
        """Terminal status and invalid filters must not leak stale dashboard state."""
        runner = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("node:assert/strict");
const html = fs.readFileSync(process.env.DASHBOARD_PATH, "utf8");
const source = html.match(/<script>([\s\S]*)<\/script>/)[1];
class Element {
  constructor() { this._value = ""; this.listeners = {}; this.children = []; }
  get options() { return this.children; }
  get selectedOptions() { return this.children.filter(option => option.selected); }
  get value() { return this.children.length ? this.selectedOptions[0]?.value || "" : this._value; }
  set value(value) { this._value = value; this.children.forEach(option => { option.selected = option.value === value; }); }
  replaceChildren(...children) { this.children = children; }
  append(child) { this.children.push(child); }
  addEventListener(name, handler) { this.listeners[name] = handler; }
}
const ids = ["service", "configuration", "source", "projection", "source-name", "refresh-button", "refresh-state",
  "source-hash", "last-imported-at", "updated-at", "error", "filter-state", "table-state",
  "positions-body", "page-summary", "previous-page", "next-page", "sort-by", "sort-order",
  "filter-strategy", "filter-symbol-family", "filter-direction", "filter-status",
  "filter-association", "date-from", "date-to", "metrics-summary", "temporal-metrics-summary", "metrics-state",
  "metric-sample-size", "metric-winning-trades", "metric-losing-trades",
  "metric-net-pnl", "metric-gross-profit", "metric-gross-loss", "metric-win-rate",
  "metric-profit-factor", "metric-payoff", "metric-expectancy",
  "metric-sharpe-per-position", "metric-sortino-per-position", "metric-sharpe-daily",
  "metric-sortino-daily", "metric-sharpe-annualized", "metric-sortino-annualized",
  "reason-sharpe-daily", "reason-sortino-daily", "reason-sharpe-annualized",
  "reason-sortino-annualized", "risk-deepest", "risk-longest", "risk-deepest-detail", "risk-longest-detail",
  "risk-percentage-deepest", "risk-percentage-longest", "risk-percentage-deepest-detail", "risk-percentage-longest-detail",
  "symbols-all", "symbols-clear", "strategies-all", "strategies-clear", "chart-granularity"];
const elements = Object.fromEntries(ids.map((id) => ["#" + id, new Element()]));
elements["#sort-by"].value = "closed_at";
elements["#sort-order"].value = "desc";
elements["#filter-status"].value = "closed";
elements["#filter-association"].value = "all";
elements["#chart-granularity"].value = "day";
let interval;
let state = "ready";
let refreshOperation = null;
let positionFetches = 0;
let positionMode = "normal";
let metricMode = "normal";
let filterMode = "normal";
const pendingPositions = [];
const pendingMetrics = [];
const pendingFilters = [];
const pendingSeries = [];
const pendingSeriesBodies = [];
const seriesSignals = [];
const seriesRequests = [];
const chartCalls = [];
let seriesMode = "normal";
const seriesPayload = {state: "available", reason: null, projection_revision: "revision",
  timezone: "America/Bahia", granularity: "day",
  effective_date_from: "2026-08-01", effective_date_to: "2026-08-01",
  periods: [{start: "2026-08-01", end: "2026-08-01", partial: false, pnl: 10, cumulative: 10}],
  strategies: [{id: '["WIN", "Turtle"]', symbol_family: "WIN", strategy: "Turtle",
    label: "WIN Turtle", points: [{pnl: 10, cumulative: 10}]}],
  drawdown: {monetary: {state: "no_drawdown", reason: null,
    points: [{at: "2026-08-01T03:00:00Z", depth: 0}]},
    percentage: {state: "unavailable", reason: "invalid_opening_balance_coverage", points: []}}};
const emptySeries = {...seriesPayload, state: "empty_sample", periods: [], strategies: [],
  drawdown: {monetary: {state: "empty_sample", reason: null, points: []},
    percentage: {state: "empty_sample", reason: null, points: []}}};
const metricPayload = {sample_size: 2, excluded_open_positions: 0, net_pnl: 10,
  percentage_drawdown: {state: "available", deepest_episode: {depth: -1.25,
    peak_at: "2026-08-01T03:00:00Z", valley_at: "2026-08-02T12:00:00Z",
    recovery_at: null, duration_days: 1}, longest_episode: {depth: -1.25,
    peak_at: "2026-08-01T03:00:00Z", valley_at: "2026-08-02T12:00:00Z",
    recovery_at: null, duration_days: 1}},
  monetary_drawdown: {state: "available", deepest_episode: {depth: -10,
    peak_at: "2026-08-01T03:00:00Z", valley_at: "2026-08-02T12:00:00Z",
    recovery_at: null, duration_days: 1}, longest_episode: {depth: -10,
    peak_at: "2026-08-01T03:00:00Z", valley_at: "2026-08-02T12:00:00Z",
    recovery_at: null, duration_days: 1}},
  gross_profit: 20, gross_loss: -10, winning_trades: 1, losing_trades: 1,
  win_rate: 0.5, profit_factor: 2, payoff: 2,
  expectancy: 5, sharpe_per_position: 0.5, sortino_per_position: 0.5,
  effective_date_from: "2026-08-01", effective_date_to: "2026-08-02",
  daily_observation_days: 2, opening_balance_required_days: 2,
  opening_balance_covered_days: 2, opening_balance_missing_dates: [],
  opening_balance_non_positive_dates: [], sharpe_daily: 0.4, sortino_daily: 0.3,
  sharpe_annualized: null, sortino_annualized: null,
  unavailable_reasons: {}};
const openMetricPayload = {sample_size: 0, excluded_open_positions: 3, net_pnl: null,
  gross_profit: null, gross_loss: null, winning_trades: null, losing_trades: null,
  win_rate: null, profit_factor: null, payoff: null,
  expectancy: null, sharpe_per_position: null, sortino_per_position: null,
  unavailable_reasons: {net_pnl: "realized_metrics_unavailable_for_open_status"}};
const statusPayload = () => ({state, configuration: "valid", source: "available",
  projection: state === "unavailable" ? "invalid" : "available", source_name: "Report.xlsx",
  timezone: "America/Bahia", projection_revision: state === "ready" ? "revision" : null,
  last_import: state === "ready" ? {source_hash: "hash", imported_at: "2026-08-01T00:00:00Z"} : null,
  refresh_operation: refreshOperation});
const context = {
  ChartView: {
    clear(message) { chartCalls.push({method: "clear", message}); },
    render(data) { chartCalls.push({method: "render", data}); },
  },
  document: {querySelector: (selector) => elements[selector], createElement: () => new Element()},
  fetch: async (url, options = {}) => {
    if (url === "/api/status") return {ok: true, status: 200, json: async () => statusPayload()};
    if (url === "/api/filter-options") {
      if (filterMode === "pending") return new Promise((resolve) => pendingFilters.push(resolve));
      return {ok: true, status: 200, json: async () => ({strategies: ["Turtle"], symbol_families: ["WIN"]})};
    }
    if (url.startsWith("/api/series?")) {
      seriesRequests.push(url);
      seriesSignals.push(options.signal);
      if (seriesMode === "pending") return new Promise(resolve => pendingSeries.push(resolve));
      if (seriesMode === "body_pending") return {ok: true,
        json: () => new Promise(resolve => pendingSeriesBodies.push(resolve))};
      if (seriesMode === "error") return {ok: false, status: 503};
      if (seriesMode === "validation") return {ok: false, status: 422,
        json: async () => ({detail: {code: "contradictory_filters"}})};
      return {ok: true, json: async () => seriesMode === "empty" ? emptySeries : seriesPayload};
    }
    if (url.startsWith("/api/metrics?")) {
      if (metricMode === "pending") return new Promise((resolve) => pendingMetrics.push(resolve));
      return {ok: true, status: 200, json: async () => metricPayload};
    }
    positionFetches += 1;
    if (positionMode === "pending") return new Promise((resolve) => pendingPositions.push(resolve));
    if (positionMode === "validation") return {ok: false, status: 422,
      json: async () => ({detail: {code: "contradictory_filters"}})};
    return {ok: true, status: 200, json: async () => ({items: [], total: 0})};
  },
  setInterval: (callback) => { interval = callback; return 1; },
  URLSearchParams, Intl, Date, console, AbortController, setTimeout, clearTimeout,
};
vm.runInNewContext(source + "\nglobalThis.loadPositions = loadPositions; globalThis.loadMetrics = loadMetrics; globalThis.loadFilterOptions = loadFilterOptions; globalThis.loadSeries = loadSeries;", context);
const flush = () => new Promise((resolve) => setImmediate(resolve));
const watchdog = setTimeout(() => {
  console.error("dashboard regression test did not complete");
  process.exitCode = 1;
}, 2000);
(async () => {
  await flush(); await flush();

  assert.deepEqual(chartCalls, [
    {method: "clear", message: "Consultando gráficos..."},
    {method: "render", data: seriesPayload},
  ]);
  assert.equal(seriesRequests.length, 1);
  assert.deepEqual([...new URLSearchParams(seriesRequests[0].split("?")[1])],
    [["projection_revision", "revision"], ["status", "closed"], ["granularity", "day"]]);

  if (elements["#risk-deepest"].textContent !== "-10,00") throw new Error("missing monetary depth");
  if (elements["#risk-longest"].textContent !== "1 dia") throw new Error("singular duration mismatch");
  if (elements["#risk-percentage-deepest"].textContent !== "-125,00%") throw new Error("missing percentage depth");
  if (!elements["#risk-longest-detail"].textContent.includes("Em andamento")) throw new Error("missing open episode state");

  refreshOperation = {operation_id: "job", state: "running", stage: "reading_source",
    created_at: new Date().toISOString(), started_at: new Date().toISOString(),
    finished_at: null, summary: null, error: null};
  await interval();
  if (!elements["#refresh-button"].disabled
      || !elements["#refresh-state"].textContent.includes("lendo fonte")) {
    throw new Error("running refresh is not visible");
  }
  refreshOperation = {...refreshOperation, state: "completed", stage: "completed",
    finished_at: new Date().toISOString()};
  await interval();
  if (elements["#refresh-button"].disabled
      || !elements["#refresh-state"].textContent.includes("concluída")) {
    throw new Error("completed refresh is not visible");
  }
  refreshOperation = {...refreshOperation, state: "error", stage: "error",
    error: {code: "validation_error", message: "Configuração ambígua"}};
  await interval();
  if (!elements["#refresh-state"].textContent.includes("Configuração ambígua")) {
    throw new Error("refresh error is not visible");
  }
  refreshOperation = null;

  // Loading clears the previous chart before a series response is available.
  seriesMode = "pending";
  const beforeLoading = chartCalls.length;
  const loadingSeries = context.loadSeries();
  assert.deepEqual(chartCalls.slice(beforeLoading), [
    {method: "clear", message: "Consultando gráficos..."},
  ]);
  pendingSeries.shift()({ok: true, json: async () => seriesPayload});
  assert.equal(await loadingSeries, true);
  assert.deepEqual(chartCalls.at(-1), {method: "render", data: seriesPayload});

  // Empty responses are forwarded unchanged; HTTP failures clear without rendering.
  seriesMode = "empty";
  const beforeEmpty = chartCalls.length;
  assert.equal(await context.loadSeries(), true);
  assert.deepEqual(chartCalls.slice(beforeEmpty), [
    {method: "clear", message: "Consultando gráficos..."},
    {method: "render", data: emptySeries},
  ]);
  seriesMode = "error";
  const beforeError = chartCalls.length;
  assert.equal(await context.loadSeries(), false);
  assert.deepEqual(chartCalls.slice(beforeError), [
    {method: "clear", message: "Consultando gráficos..."},
    {method: "clear", message: "Gráficos indisponíveis (HTTP 503)."},
  ]);

  // A late successful response cannot replace a newer chart.
  seriesMode = "pending";
  const olderSeries = context.loadSeries();
  const newerSeries = context.loadSeries();
  const newSeriesPayload = {...seriesPayload, projection_revision: "new-revision"};
  pendingSeries[1]({ok: true, json: async () => newSeriesPayload});
  assert.equal(await newerSeries, true);
  const beforeStaleSeries = chartCalls.length;
  pendingSeries[0]({ok: true, json: async () => seriesPayload});
  assert.equal(await olderSeries, false);
  assert.equal(chartCalls.length, beforeStaleSeries);
  assert.equal(chartCalls.at(-1).data, newSeriesPayload);
  pendingSeries.length = 0;
  seriesMode = "normal";

  seriesMode = "validation";
  const beforeValidation = chartCalls.length;
  assert.equal(await context.loadSeries(), "handled");
  assert.deepEqual(chartCalls.slice(beforeValidation), [
    {method: "clear", message: "Consultando gráficos..."},
    {method: "clear", message: "Gráficos indisponíveis (Uma estratégia não pode ser combinada com posições não associadas.)."},
  ]);

  // Bound both a stalled response and a stalled JSON body without waiting 15 seconds.
  for (const mode of ["pending", "body_pending"]) {
    seriesMode = mode;
    let expire;
    const normalSetTimeout = context.setTimeout;
    const normalClearTimeout = context.clearTimeout;
    context.setTimeout = (callback, delay) => {
      assert.equal(delay, 15000);
      expire = callback;
      return 0;
    };
    let timerCleared = false;
    context.clearTimeout = () => { timerCleared = true; };
    const beforeTimeout = chartCalls.length;
    const stalled = context.loadSeries();
    await flush();
    expire();
    assert.equal(await stalled, false);
    assert.equal(seriesSignals.at(-1).aborted, true);
    assert.equal(timerCleared, true);
    assert.equal(chartCalls.length, beforeTimeout + 2);
    assert.match(chartCalls.at(-1).message, /Tempo limite excedido/);
    const afterTimeout = chartCalls.length;
    if (mode === "pending") pendingSeries.shift()({ok: true, json: async () => seriesPayload});
    else pendingSeriesBodies.shift()(seriesPayload);
    await flush();
    assert.equal(chartCalls.length, afterTimeout);
    context.setTimeout = normalSetTimeout;
    context.clearTimeout = normalClearTimeout;
    seriesMode = "normal";
    const beforeRetry = seriesRequests.length;
    await interval();
    assert.equal(seriesRequests.length, beforeRetry + 1);
    assert.equal(chartCalls.at(-1).data, seriesPayload);
  }

  metricMode = "pending";
  elements["#filter-strategy"].value = "strategy:Turtle";
  elements["#filter-strategy"].listeners.change();
  await flush();
  if (elements["#metric-net-pnl"].textContent !== "—") throw new Error("old metrics remained visible during a filter change");
  if (elements["#risk-percentage-deepest"].textContent !== "—") throw new Error("old percentage remained visible");
  pendingMetrics.shift()({ok: true, status: 200, json: async () => metricPayload});
  metricMode = "normal";
  await flush(); await flush();

  metricMode = "pending";
  const olderMetrics = context.loadMetrics();
  const newerMetrics = context.loadMetrics();
  pendingMetrics[1]({ok: true, status: 200, json: async () => ({...metricPayload,
    monetary_drawdown: {...metricPayload.monetary_drawdown,
      deepest_episode: {...metricPayload.monetary_drawdown.deepest_episode, depth: -30}},
    percentage_drawdown: {...metricPayload.percentage_drawdown,
      deepest_episode: {...metricPayload.percentage_drawdown.deepest_episode, depth: -0.3}}})});
  await flush();
  pendingMetrics[0]({ok: true, status: 200, json: async () => metricPayload});
  await Promise.all([olderMetrics, newerMetrics]);
  pendingMetrics.length = 0;
  if (elements["#risk-deepest"].textContent !== "-30,00"
      || elements["#risk-percentage-deepest"].textContent !== "-30,00%") {
    throw new Error("stale risk response overwrote the current sample");
  }

  positionMode = "pending";
  context.loadPositions();
  metricMode = "pending";
  context.loadMetrics();
  filterMode = "pending";
  context.loadFilterOptions();
  seriesMode = "pending";
  const interruptedSeries = context.loadSeries();
  state = "unavailable";
  await interval();
  assert.deepEqual(chartCalls.at(-1), {method: "clear", message: "Projeção indisponível."});
  const beforeInterruptedSeries = chartCalls.length;
  pendingSeries.shift()({ok: true, json: async () => seriesPayload});
  assert.equal(await interruptedSeries, false);
  assert.equal(chartCalls.length, beforeInterruptedSeries);
  pendingPositions[0]({ok: true, status: 200, json: async () => ({items: [{position_id: "stale",
    strategy: "Turtle", association: "associated", symbol_family: "WIN", direction: "buy",
    opened_at: null, closed_at: null, status: "closed", realized_pnl: 1}], total: 1})});
  pendingMetrics.shift()({ok: true, status: 200, json: async () => ({...metricPayload, net_pnl: 999})});
  pendingFilters[0]({ok: true, status: 200, json: async () => ({strategies: ["Stale"], symbol_families: ["OLD"]})});
  await flush(); await flush();
  if (elements["#page-summary"].textContent !== "Projeção indisponível.") throw new Error("stale position escaped terminal state");
  if (elements["#metric-net-pnl"].textContent !== "—") throw new Error("stale metric escaped terminal state");
  if (elements["#risk-deepest"].textContent !== "—") throw new Error("stale drawdown escaped terminal state");
  if (elements["#risk-percentage-deepest"].textContent !== "—") throw new Error("stale percentage escaped terminal state");
  if (elements["#filter-strategy"].children.some((option) => option.value === "Stale")) throw new Error("stale filter catalog escaped terminal state");

  positionMode = "normal";
  metricMode = "normal";
  seriesMode = "normal";
  elements["#date-from"].value = "2026-08-03";
  elements["#date-to"].value = "2026-08-01";
  const beforeInvalidDates = positionFetches;
  const seriesBeforeInvalidDates = seriesRequests.length;
  elements["#date-from"].listeners.change();
  await flush();
  if (positionFetches !== beforeInvalidDates) throw new Error("invalid dates reached API");
  assert.equal(seriesRequests.length, seriesBeforeInvalidDates);
  assert.match(chartCalls.at(-1).message, /data inicial/);
  if (!elements["#table-state"].textContent.includes("data inicial")) throw new Error("missing date validation detail");

  elements["#date-from"].value = "";
  elements["#date-to"].value = "";
  elements["#filter-strategy"].value = "strategy:Turtle";
  elements["#filter-association"].value = "unassociated";
  elements["#filter-association"].listeners.change();
  await flush();
  if (elements["#filter-strategy"].value !== "strategy:Turtle") throw new Error("strategy selection changed silently");

  elements["#filter-strategy"].value = "strategy:Turtle";
  elements["#filter-strategy"].listeners.change();
  await flush();
  if (elements["#filter-association"].value !== "unassociated") throw new Error("association selection changed silently");

  positionMode = "validation";
  filterMode = "normal";
  const before422 = positionFetches;
  state = "ready";
  await interval();
  if (!elements["#table-state"].textContent.includes("não associadas")) throw new Error("API detail was not exposed");
  if (positionFetches !== before422 + 1) throw new Error("expected one recovery validation request");
  await interval();
  if (positionFetches !== before422 + 1) throw new Error("HTTP 422 caused polling retry");
  clearTimeout(watchdog);
})().catch((error) => { clearTimeout(watchdog); console.error(error); process.exitCode = 1; });
"""
        node_executable = NODE_EXECUTABLE
        if node_executable is None:
            self.skipTest("requires Node.js for JavaScript execution")
        result = subprocess.run(  # nosec B603 -- absolute executable and fixed test script
            [node_executable, "-e", runner],
            check=False,
            capture_output=True,
            env={"DASHBOARD_PATH": str(Path(dashboard().path))},
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(NODE_EXECUTABLE, "requires Node.js for JavaScript execution")
    def test_dashboard_synchronizes_filters_pagination_and_async_recovery(self) -> None:
        """Keep filters, pagination, and async recovery in one dashboard state."""
        runner = r"""
const fs = require("fs");
const vm = require("vm");
const html = fs.readFileSync(process.env.DASHBOARD_PATH, "utf8");
const assert = require("node:assert/strict");
const source = html.match(/<script>([\s\S]*)<\/script>/)[1];
class Element {
  constructor() { this._value = ""; this.listeners = {}; this.children = []; }
  get options() { return this.children; }
  get selectedOptions() { return this.children.filter(option => option.selected); }
  get value() { return this.children.length ? this.selectedOptions[0]?.value || "" : this._value; }
  set value(value) { this._value = value; this.children.forEach(option => { option.selected = option.value === value; }); }
  replaceChildren(...children) { this.children = children; }
  append(child) { (this.children ||= []).push(child); }
  addEventListener(name, handler) { this.listeners[name] = handler; }
}
const ids = ["service", "configuration", "source", "projection", "source-name", "refresh-button", "refresh-state",
  "source-hash", "last-imported-at", "updated-at", "error", "filter-state", "table-state",
  "positions-body", "page-summary", "previous-page", "next-page", "sort-by", "sort-order",
  "filter-strategy", "filter-symbol-family", "filter-direction", "filter-status",
  "filter-association", "date-from", "date-to", "metrics-summary", "temporal-metrics-summary", "metrics-state",
  "metric-sample-size", "metric-winning-trades", "metric-losing-trades",
  "metric-net-pnl", "metric-gross-profit", "metric-gross-loss", "metric-win-rate",
  "metric-profit-factor", "metric-payoff", "metric-expectancy",
  "metric-sharpe-per-position", "metric-sortino-per-position", "metric-sharpe-daily",
  "metric-sortino-daily", "metric-sharpe-annualized", "metric-sortino-annualized",
  "reason-sharpe-daily", "reason-sortino-daily", "reason-sharpe-annualized",
  "reason-sortino-annualized", "risk-deepest", "risk-longest", "risk-deepest-detail", "risk-longest-detail",
  "risk-percentage-deepest", "risk-percentage-longest", "risk-percentage-deepest-detail", "risk-percentage-longest-detail",
  "symbols-all", "symbols-clear", "strategies-all", "strategies-clear", "chart-granularity"];
const elements = Object.fromEntries(ids.map((id) => ["#" + id, new Element()]));
elements["#sort-by"].value = "closed_at";
elements["#sort-order"].value = "desc";
elements["#filter-status"].value = "closed";
elements["#filter-association"].value = "all";
elements["#chart-granularity"].value = "day";
let state = "ready";
let publishedRevision = "revision";
let revisionConflictEndpoint = null;
let revisionChanges = 0;
const revisionRequests = [];
let interval;
let positionFetches = 0;
let filterFetches = 0;
let filterPayload = {strategies: ["FVG", "Turtle"], symbol_families: ["WDO", "WIN"]};
let filterMode = "normal";
let lastPositionUrl = "";
let metricFetches = 0;
let lastMetricUrl = "";
let lastSeriesUrl = "";
let lastSeriesPayload;
let seriesFetches = 0;
const chartCalls = [];
const seriesPayload = {state: "available", reason: null, projection_revision: "revision",
  timezone: "America/Bahia", granularity: "day",
  effective_date_from: "2026-08-01", effective_date_to: "2026-08-02",
  periods: [{start: "2026-08-01", end: "2026-08-01", partial: false, pnl: 20, cumulative: 20},
    {start: "2026-08-02", end: "2026-08-02", partial: false, pnl: -10, cumulative: 10}],
  strategies: [{id: '["WIN", "FVG"]', symbol_family: "WIN", strategy: "FVG",
    label: "WIN FVG", points: [{pnl: 20, cumulative: 20}, {pnl: -10, cumulative: 10}]}],
  drawdown: {monetary: {state: "available", reason: null,
    points: [{at: "2026-08-01T12:00:00Z", depth: 0}, {at: "2026-08-02T12:00:00Z", depth: -10}]},
    percentage: {state: "unavailable", reason: "invalid_opening_balance_coverage", points: []}}};
let mode = "retry";
const pending = [];
let statusMode = "normal";
const pendingStatuses = [];
const metricPayload = {sample_size: 2, excluded_open_positions: 0, net_pnl: 10,
  gross_profit: 20, gross_loss: -10, winning_trades: 1, losing_trades: 1,
  win_rate: 0.5, profit_factor: 2, payoff: 2,
  expectancy: 5, sharpe_per_position: 0.5, sortino_per_position: 0.5,
  effective_date_from: "2026-08-01", effective_date_to: "2026-08-02",
  daily_observation_days: 2, opening_balance_required_days: 2,
  opening_balance_covered_days: 2, opening_balance_missing_dates: [],
  opening_balance_non_positive_dates: [], sharpe_daily: 0.4, sortino_daily: 0.3,
  sharpe_annualized: null, sortino_annualized: null,
  unavailable_reasons: {}};
const openMetricPayload = {sample_size: 0, excluded_open_positions: 3, net_pnl: null,
  gross_profit: null, gross_loss: null, winning_trades: null, losing_trades: null,
  win_rate: null, profit_factor: null, payoff: null,
  expectancy: null, sharpe_per_position: null, sortino_per_position: null,
  unavailable_reasons: {net_pnl: "realized_metrics_unavailable_for_open_status"}};
const payload = () => ({state, configuration: "valid", source: "available",
  projection: state === "unavailable" ? "invalid" : "available", source_name: "Report.xlsx",
  timezone: "America/Bahia", projection_revision: state === "ready" ? publishedRevision : null,
  last_import: state === "ready" ? {source_hash: "same-hash", imported_at: "2026-08-01T00:00:00+00:00"} : null});
const context = {
  ChartView: {
    clear(message) { chartCalls.push({method: "clear", message}); },
    render(data) { chartCalls.push({method: "render", data}); },
  },
  document: {querySelector: (selector) => elements[selector], createElement: () => new Element()},
  fetch: async (url) => {
    if (url === "/api/status") {
      if (statusMode === "race") return new Promise((resolve) => pendingStatuses.push(resolve));
      return {ok: true, json: async () => payload()};
    }
    if (url === "/api/filter-options") {
      filterFetches += 1;
      if (filterMode === "failure") return {ok: false, json: async () => ({})};
      return {ok: true, json: async () => filterPayload};
    }
    if (["positions", "metrics", "series"].some(name => url.startsWith(`/api/${name}?`))) {
      revisionRequests.push(url);
      if (revisionConflictEndpoint && url.startsWith(`/api/${revisionConflictEndpoint}?`)) {
        revisionConflictEndpoint = null;
        publishedRevision = `coherent-${++revisionChanges}`;
        return {ok: false, status: 409,
          json: async () => ({detail: {code: "projection_revision_changed"}})};
      }
    }
    if (url.startsWith("/api/series?")) {
      seriesFetches += 1;
      lastSeriesUrl = url;
      const query = new URLSearchParams(url.split("?")[1]);
      lastSeriesPayload = {...seriesPayload, granularity: query.get("granularity"),
        projection_revision: query.get("projection_revision")};
      const data = lastSeriesPayload;
      return {ok: true, json: async () => data};
    }
    if (url.startsWith("/api/metrics?")) {
      metricFetches += 1;
      lastMetricUrl = url;
      return {ok: true, json: async () => url.includes("status=open") ? openMetricPayload : metricPayload};
    }
    positionFetches += 1;
    lastPositionUrl = url;
    if (mode === "retry" && positionFetches === 1) return {ok: false, json: async () => ({})};
    if (mode === "failure") return {ok: false, json: async () => ({})};
    if (mode === "race") return new Promise((resolve) => pending.push(resolve));
    return {ok: true, json: async () => ({items: [], total: 120})};
  },
  setInterval: (callback) => { interval = callback; return 1; },
  URLSearchParams, Intl, Date, console, AbortController, setTimeout, clearTimeout,
};
vm.runInNewContext(source + "\nglobalThis.loadStatus = loadStatus; globalThis.formatMetric = formatMetric; globalThis.renderMetrics = renderMetrics; globalThis.loadFilterOptions = loadFilterOptions; globalThis.buildFilterQuery = buildFilterQuery;", context);
const flush = () => new Promise((resolve) => setImmediate(resolve));
(async () => {
  await flush(); await flush();
  if (context.formatMetric("winning_trades", undefined) !== "—") throw new Error("missing metric rendered as NaN");
  if (filterFetches !== 1) throw new Error(`expected initial filter catalog, got ${filterFetches}`);
  if (!lastPositionUrl.includes("status=closed")) throw new Error(`missing default closed filter: ${lastPositionUrl}`);
  if (metricFetches !== 1) throw new Error(`expected initial metrics, got ${metricFetches}`);
  if (elements["#metric-sample-size"].textContent !== "2") throw new Error("operation count formatting mismatch");
  if (elements["#metric-winning-trades"].textContent !== "1") throw new Error("winning count formatting mismatch");
  if (elements["#metric-losing-trades"].textContent !== "1") throw new Error("losing count formatting mismatch");
  if (elements["#metric-net-pnl"].textContent !== "10,00") throw new Error("metric formatting mismatch");
  if (elements["#metric-gross-loss"].textContent !== "-10,00") throw new Error("gross loss formatting mismatch");
  if (elements["#metric-win-rate"].textContent !== "50,00%") throw new Error("percentage formatting mismatch");
  if (elements["#metric-profit-factor"].textContent !== "2,000") throw new Error("ratio formatting mismatch");
  if (elements["#metrics-summary"].textContent !== "2 posições realizadas.") throw new Error("count formatting mismatch");
  await interval();
  if (positionFetches !== 2) throw new Error(`expected retry after failed load, got ${positionFetches}`);
  if (metricFetches !== 2) throw new Error(`expected metrics with retry, got ${metricFetches}`);
  state = "unavailable";
  await interval();
  if (elements["#page-summary"].textContent !== "Projeção indisponível.") throw new Error("missing unavailable table state");
  if (elements["#table-state"].hidden || !elements["#table-state"].textContent.includes("projeção SQLite está indisponível")) throw new Error("missing unavailable error notice");
  if (!elements["#previous-page"].disabled || !elements["#next-page"].disabled) throw new Error("pagination remains enabled");
  state = "ready";
  filterMode = "failure";
  await interval();
  if (positionFetches !== 3) throw new Error(`expected recovery fetch, got ${positionFetches}`);
  filterMode = "normal";
  await interval();
  if (filterFetches !== 3) throw new Error(`expected filter retry, got ${filterFetches}`);
  if (positionFetches !== 4) throw new Error(`expected reload after filter recovery, got ${positionFetches}`);
  const metricsBeforePagination = metricFetches;
  elements["#next-page"].listeners.click();
  await flush();
  if (!lastPositionUrl.includes("offset=50")) throw new Error(`pagination did not advance: ${lastPositionUrl}`);
  if (metricFetches !== metricsBeforePagination) throw new Error("pagination reloaded metrics");
  elements["#filter-strategy"].value = "strategy:FVG";
  elements["#filter-symbol-family"].value = "WIN";
  elements["#filter-direction"].value = "buy";
  elements["#filter-association"].value = "associated";
  elements["#date-from"].value = "2026-08-01";
  elements["#date-to"].value = "2026-08-31";
  elements["#filter-strategy"].listeners.change();
  await flush();
  for (const token of ["strategy=FVG", "symbol_family=WIN", "direction=buy",
    "association=associated", "date_from=2026-08-01", "date_to=2026-08-31", "offset=0"]) {
    if (!lastPositionUrl.includes(token)) throw new Error(`missing filter ${token}: ${lastPositionUrl}`);
  }
  for (const token of ["strategy=FVG", "symbol_family=WIN", "direction=buy",
    "association=associated", "date_from=2026-08-01", "date_to=2026-08-31"]) {
    if (!lastMetricUrl.includes(token)) throw new Error(`missing metric filter ${token}: ${lastMetricUrl}`);
    if (!lastSeriesUrl.includes(token)) throw new Error(`missing series filter ${token}: ${lastSeriesUrl}`);
  }
  if (lastMetricUrl.includes("offset=") || lastMetricUrl.includes("sort_by=")) throw new Error(`table state leaked into metrics: ${lastMetricUrl}`);
  assert.equal(chartCalls.at(-1).method, "render");
  assert.equal(chartCalls.at(-1).data, lastSeriesPayload);
  assert.deepEqual(chartCalls.at(-1).data.strategies, seriesPayload.strategies);
  if (elements["#table-state"].hidden !== true || elements["#table-state"].textContent !== "") throw new Error("stale unavailable notice");
  mode = "failure";
  const metricsBeforeSort = metricFetches;
  elements["#sort-by"].listeners.change();
  await flush();
  if (metricFetches !== metricsBeforeSort) throw new Error("sorting reloaded metrics");
  mode = "normal";
  await interval();
  if (positionFetches !== 8) throw new Error(`expected retry after active position failure, got ${positionFetches}`);
  if (elements["#filter-strategy"].value !== "strategy:FVG") throw new Error("valid strategy selection was lost");
  mode = "race";
  elements["#sort-by"].value = "opened_at";
  elements["#sort-by"].listeners.change();
  elements["#sort-by"].value = "status";
  elements["#sort-by"].listeners.change();
  if (pending.length !== 2) throw new Error(`expected 2 pending requests, got ${pending.length}`);
  pending[1]({ok: true, json: async () => ({items: [{position_id: "new", strategy: null, symbol_family: null, direction: "buy", opened_at: null, closed_at: null, status: "open", realized_pnl: null}], total: 1})});
  await flush();
  pending[0]({ok: true, json: async () => ({items: [{position_id: "old", strategy: null, symbol_family: null, direction: "buy", opened_at: null, closed_at: null, status: "open", realized_pnl: null}], total: 1})});
  await flush();
  if (elements["#positions-body"].children[0].children[0].textContent !== "new") throw new Error("stale response overwrote current table");
  mode = "normal";
  statusMode = "race";
  elements["#filter-strategy"].value = "strategy:FVG";
  filterPayload = {strategies: ["Turtle"], symbol_families: ["WIN"]};
  const olderStatus = context.loadStatus();
  const newerStatus = context.loadStatus();
  if (pendingStatuses.length !== 2) throw new Error(`expected 2 pending status requests, got ${pendingStatuses.length}`);
  pendingStatuses[1]({ok: true, json: async () => ({state: "ready", configuration: "valid", source: "available", projection: "available", source_name: "Report.xlsx", timezone: "America/Bahia", projection_revision: "new-revision", last_import: {source_hash: "same-hash", imported_at: "2026-08-02T00:00:00+00:00"}})});
  await flush();
  pendingStatuses[0]({ok: true, json: async () => ({state: "unavailable", configuration: "valid", source: "available", projection: "invalid", source_name: "Report.xlsx", timezone: "America/Bahia", projection_revision: null, last_import: null})});
  await Promise.all([olderStatus, newerStatus]);
  if (elements["#service"].textContent !== "pronto") throw new Error("stale status overwrote current state");
  if (elements["#filter-strategy"].value !== "strategy:FVG") throw new Error("removed strategy selection was lost");
  if (!elements["#filter-strategy"].selectedOptions[0].textContent.includes("indisponível")) throw new Error("removed strategy is not identified");
  statusMode = "normal";
  elements["#filter-status"].value = "open";
  elements["#filter-status"].listeners.change();
  await flush(); await flush();
  if (elements["#metric-net-pnl"].textContent !== "—") throw new Error("open status displayed realized P&L");
  if (!elements["#metrics-state"].textContent.includes("indisponíveis")) throw new Error("open status reason is not visible");
  context.renderMetrics({...metricPayload, profit_factor: null, payoff: null,
    sharpe_per_position: null, sortino_per_position: null,
    unavailable_reasons: {profit_factor: "no_losing_positions", payoff: "no_losing_positions",
      sharpe_per_position: "insufficient_sample", sortino_per_position: "zero_downside_deviation"}});
  const unavailableCards = {
    "#metric-profit-factor": "A amostra não contém posições perdedoras.",
    "#metric-payoff": "A amostra não contém posições perdedoras.",
    "#metric-sharpe-per-position": "São necessárias pelo menos duas posições realizadas.",
    "#metric-sortino-per-position": "A amostra não possui dispersão negativa.",
  };
  for (const [selector, title] of Object.entries(unavailableCards)) {
    if (elements[selector].textContent !== "—") throw new Error(`${selector} displayed an unavailable value`);
    if (elements[selector].title !== title) throw new Error(`${selector} has the wrong unavailable reason`);
  }
  if (elements["#metric-net-pnl"].textContent !== "10,00") throw new Error("available metric was cleared");
  for (const message of ["posições perdedoras", "pelo menos duas", "dispersão negativa"]) {
    if (!elements["#metrics-state"].textContent.includes(message)) throw new Error(`missing reason: ${message}`);
  }
  const legacyPayload = {...metricPayload};
  delete legacyPayload.daily_observation_days;
  context.renderMetrics(legacyPayload);
  for (const [state, expected] of [["empty_sample", "Sem dados"],
    ["no_drawdown", "Sem drawdown"], ["unavailable", "Indisponível"]]) {
    context.renderMetrics({...metricPayload, monetary_drawdown: {state,
      deepest_episode: null, longest_episode: null}});
    if (elements["#risk-deepest"].textContent !== "—") throw new Error("fabricated episode");
    if (!elements["#risk-longest-detail"].textContent.includes(expected)) throw new Error("missing risk state");
  }
  const recovered = {depth: -20, peak_at: "2026-08-01T12:00:00Z",
    valley_at: "2026-08-02T12:00:00Z", recovery_at: "2026-08-03T12:00:00Z", duration_days: 2};
  context.renderMetrics({...metricPayload, monetary_drawdown: {state: "available",
    deepest_episode: recovered, longest_episode: recovered}});
  if (elements["#risk-longest"].textContent !== "2 dias") throw new Error("missing civil duration");
  if (elements["#risk-deepest-detail"].textContent.includes("Em andamento")) throw new Error("recovered episode marked open");
  if (!elements["#risk-deepest-detail"].textContent.includes("03/08/2026")) throw new Error("missing recovery date");
  if (!elements["#risk-percentage-deepest-detail"].textContent.includes("não fornecido")) {
    throw new Error("legacy percentage absence was not reported independently");
  }
  const percentRecovered = {...recovered, depth: -0.2};
  context.renderMetrics({...metricPayload,
    monetary_drawdown: {state: "available", deepest_episode: recovered, longest_episode: recovered},
    percentage_drawdown: {state: "available", deepest_episode: percentRecovered, longest_episode: percentRecovered}});
  if (elements["#risk-percentage-deepest"].textContent !== "-20,00%"
      || elements["#risk-percentage-longest"].textContent !== "2 dias"
      || elements["#risk-percentage-longest-detail"].textContent.includes("Em andamento")) {
    throw new Error("recovered percentage episode was rendered incorrectly");
  }
  for (const [state, expected] of [["empty_sample", "Sem dados"],
    ["no_drawdown", "Sem drawdown"], ["unavailable", "cobertura"]]) {
    context.renderMetrics({...metricPayload,
      monetary_drawdown: {state: "available", deepest_episode: recovered, longest_episode: recovered},
      percentage_drawdown: {state, deepest_episode: null, longest_episode: null},
      unavailable_reasons: {percentage_drawdown: "invalid_opening_balance_coverage"}});
    if (elements["#risk-deepest"].textContent !== "-20,00") throw new Error("percentage state cleared monetary risk");
    if (elements["#risk-percentage-deepest"].textContent !== "—"
        || !elements["#risk-percentage-longest-detail"].textContent.includes(expected)) {
      throw new Error("missing percentage state detail");
    }
  }
  context.renderMetrics(legacyPayload);
  if (!elements["#temporal-metrics-summary"].textContent.includes("não fornecidas")) {
    throw new Error("legacy backend was presented as real zero coverage");
  }
  context.renderMetrics({...metricPayload, sharpe_daily: null, sortino_daily: null,
    sharpe_annualized: null, sortino_annualized: null,
    opening_balance_required_days: 2, opening_balance_covered_days: 1,
    opening_balance_missing_dates: ["2026-08-02"], opening_balance_non_positive_dates: [],
    unavailable_reasons: {}, temporal_unavailable_reasons: {sharpe_daily: "invalid_opening_balance_coverage",
      sortino_daily: "invalid_opening_balance_coverage",
      sharpe_annualized: "invalid_opening_balance_coverage",
      sortino_annualized: "invalid_opening_balance_coverage"}});
  if (elements["#metric-sharpe-daily"].title !== "A cobertura do saldo de abertura ajustado é inválida.") {
    throw new Error("daily coverage reason is not visible");
  }
  if (elements["#reason-sharpe-daily"].textContent !== "A cobertura do saldo de abertura ajustado é inválida.") {
    throw new Error("daily coverage reason is not associated with its card");
  }
  if (!elements["#temporal-metrics-summary"].textContent.includes("2026-08-02")) {
    throw new Error("missing balance date is not visible");
  }

  // Exercise native-style multi-selection through the actual change handlers.
  filterPayload = {strategies: ["FVG", "Turtle"], symbol_families: ["WDO", "WIN"]};
  await context.loadFilterOptions();
  elements["#filter-status"].value = "closed";
  elements["#filter-association"].value = "all";
  function selectValues(selector, values) {
    const control = elements[selector];
    control.options.forEach(option => { option.selected = values.includes(option.value); });
    control.listeners.change();
  }
  function analyticalQueries() {
    return [lastPositionUrl, lastMetricUrl, lastSeriesUrl]
      .map(url => new URLSearchParams(url.split("?")[1]));
  }
  selectValues("#filter-strategy", ["strategy:FVG", "strategy:Turtle"]);
  selectValues("#filter-symbol-family", ["WDO", "WIN"]);
  await flush(); await flush();
  for (const query of analyticalQueries()) {
    assert.deepEqual(query.getAll("strategy"), ["FVG", "Turtle"]);
    assert.deepEqual(query.getAll("symbol_family"), ["WDO", "WIN"]);
    assert.equal(query.has("include_unclassified"), false);
    assert.equal(query.has("selection_empty"), false);
    for (const [name, value] of [["direction", "buy"], ["status", "closed"],
      ["date_from", "2026-08-01"], ["date_to", "2026-08-31"]]) {
      assert.deepEqual(query.getAll(name), [value]);
    }
  }
  for (const name of ["limit", "offset", "sort_by", "sort_order"]) {
    assert.equal(analyticalQueries()[2].has(name), false);
  }
  assert.equal(chartCalls.at(-1).data, lastSeriesPayload);
  assert.equal(chartCalls.at(-1).data.periods.at(-1).cumulative, 10);

  // Period changes reload only series and forward the corresponding payload.
  const beforeGranularity = [positionFetches, metricFetches, seriesFetches];
  for (const granularity of ["day", "week", "month", "year"]) {
    elements["#chart-granularity"].value = granularity;
    elements["#chart-granularity"].listeners.change();
    await flush();
    assert.deepEqual(analyticalQueries()[2].getAll("granularity"), [granularity]);
    assert.equal(chartCalls.at(-1).data, lastSeriesPayload);
    assert.equal(chartCalls.at(-1).data.granularity, granularity);
  }
  assert.deepEqual([positionFetches, metricFetches, seriesFetches],
    [beforeGranularity[0], beforeGranularity[1], beforeGranularity[2] + 4]);

  // The residual category has its own flag, never an ordinary strategy value.
  for (const selected of [["unclassified:"], ["strategy:FVG", "unclassified:"]]) {
    selectValues("#filter-strategy", selected);
    await flush();
    for (const query of analyticalQueries()) {
      assert.deepEqual(query.getAll("include_unclassified"), ["true"]);
      assert.deepEqual(query.getAll("strategy"), selected.length === 1 ? [] : ["FVG"]);
      assert.deepEqual(query.getAll("symbol_family"), ["WDO", "WIN"]);
      assert.equal(query.has("selection_empty"), false);
    }
  }

  // Clearing either selector is explicit emptiness, not an implicit "all" query.
  for (const [button, selector, restore] of [
    ["#strategies-clear", "#filter-strategy", "#strategies-all"],
    ["#symbols-clear", "#filter-symbol-family", "#symbols-all"],
  ]) {
    const beforeClear = [positionFetches, metricFetches, seriesFetches];
    const rendersBeforeClear = chartCalls.filter(call => call.method === "render").length;
    elements[button].listeners.click();
    await flush();
    assert.equal(elements[selector].selectedOptions.length, 0);
    assert.equal(context.buildFilterQuery().get("selection_empty"), "true");
    assert.deepEqual([positionFetches, metricFetches, seriesFetches], beforeClear);
    assert.equal(chartCalls.filter(call => call.method === "render").length, rendersBeforeClear);
    assert.deepEqual(chartCalls.at(-1), {method: "clear", message: "Nenhuma combinação selecionada."});
    assert.equal(elements["#metric-net-pnl"].textContent, "—");
    assert.equal(elements["#positions-body"].children.length, 0);
    elements[restore].listeners.click();
    await flush();
    assert.equal(elements[selector].selectedOptions.length, elements[selector].options.length);
    assert.equal(context.buildFilterQuery().has("selection_empty"), false);
    assert.equal(chartCalls.at(-1).method, "render");
  }
  for (const query of analyticalQueries()) {
    for (const name of ["strategy", "symbol_family", "include_unclassified", "selection_empty"]) {
      assert.equal(query.has(name), false);
    }
  }
  assert.equal(elements["#filter-strategy"].selectedOptions.some(option => option.value === "unclassified:"), true);

  // A publication race at any resource clears the whole sample and pins its reload.
  for (const resource of ["positions", "metrics", "series"]) {
    revisionConflictEndpoint = resource;
    const beforeConflict = chartCalls.length;
    const beforeRequests = revisionRequests.length;
    elements["#filter-direction"].value = resource === "metrics" ? "sell" : "buy";
    elements["#filter-direction"].listeners.change();
    await flush(); await flush(); await flush();
    const reset = chartCalls.findLastIndex(call => call.message === "Amostra atualizada; recarregando...");
    assert.ok(reset >= beforeConflict, `missing sample invalidation for ${resource}`);
    assert.equal(chartCalls.at(-1).method, "render");
    assert.equal(chartCalls.at(-1).data.projection_revision, publishedRevision);
    assert.ok(chartCalls.slice(reset).filter(call => call.method === "render")
      .every(call => call.data.projection_revision === publishedRevision));
    for (const query of analyticalQueries()) {
      assert.deepEqual(query.getAll("projection_revision"), [publishedRevision]);
    }
    assert.ok(revisionRequests.slice(beforeRequests).every(url =>
      new URLSearchParams(url.split("?")[1]).getAll("projection_revision").length === 1));
  }

})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        node_executable = NODE_EXECUTABLE
        if node_executable is None:
            self.skipTest("requires Node.js for JavaScript execution")
        result = subprocess.run(  # nosec B603 -- absolute executable and fixed test script
            [node_executable, "-e", runner],
            check=False,
            capture_output=True,
            env={"DASHBOARD_PATH": str(Path(dashboard().path))},
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fastapi_serves_dashboard_over_http(self) -> None:
        """FastAPI should expose the dashboard at the root endpoint."""

        async def request_dashboard() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.get("/")

        response = asyncio.run(request_dashboard())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertIn("Dashboard local", response.text)

    def test_refresh_api_runs_and_persists_a_successful_operation(self) -> None:
        """POST returns an ID and its status reaches a durable successful result."""
        self._write_config()
        workbook(self.source_dir / "ReportHistory.xlsx")

        with self._paths():
            response = self._request("POST", "/api/refresh")
            self.assertEqual(response.status_code, 202)
            accepted = response.json()
            result = self._wait_for_refresh(accepted["operation_id"])
            status = self._request("GET", "/api/status").json()

        self.assertEqual(accepted["status_url"], f"/api/refresh/{accepted['operation_id']}")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["summary"]["positions_created"], 2)
        self.assertEqual(status["refresh_operation"]["operation_id"], accepted["operation_id"])

    def test_refresh_api_reports_configuration_failure_without_losing_operation(self) -> None:
        """Invalid YAML finishes as a queryable controlled error."""
        (self.config_dir / "config.yaml").write_text("timezone: [", encoding="utf-8")

        with self._paths():
            response = self._request("POST", "/api/refresh")
            result = self._wait_for_refresh(response.json()["operation_id"])

        self.assertEqual(response.status_code, 202)
        self.assertEqual(result["state"], "error")
        self.assertEqual(result["error"]["code"], "configuration_error")
        self.assertIn("interpretar", result["error"]["message"])

    def test_refresh_recovers_when_terminal_state_write_fails_after_publication(self) -> None:
        """Published data reconciles as success with its prepared summary."""
        self._write_config()
        workbook(self.source_dir / "ReportHistory.xlsx")
        original_complete = RefreshOperationStore.complete
        failed_once = False

        def flaky_complete(
            store: RefreshOperationStore,
            operation_id: str,
            summary: dict[str, Any] | None = None,
            *,
            stage: str = "completed",
        ) -> None:
            nonlocal failed_once
            if not failed_once and stage == "completed":
                failed_once = True
                raise sqlite3.OperationalError("injected terminal write failure")
            original_complete(store, operation_id, summary, stage=stage)

        with self._paths(), patch.object(
            RefreshOperationStore, "complete", autospec=True, side_effect=flaky_complete
        ):
            response = self._request("POST", "/api/refresh")
            result = self._wait_for_refresh(response.json()["operation_id"])

        self.assertTrue(failed_once)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["stage"], "completed_after_restart")
        self.assertEqual(result["summary"]["positions_created"], 2)

    def test_refresh_api_rejects_a_concurrent_attempt(self) -> None:
        """A second POST receives a conflict while the first owns the import lock."""
        self._write_config()
        entered = threading.Event()
        release = threading.Event()

        def slow_refresh(
            _service: object,
            database_path: str | Path,
            **_kwargs: object,
        ) -> ImportSummary:
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test refresh was not released")
            return ImportSummary("hash", 1, 1, 0, 0, Path(database_path))

        with self._paths(), patch(
            "algobotdash.refresh_runner.ImportService.refresh_with_lock",
            autospec=True,
            side_effect=slow_refresh,
        ):
            first = self._request("POST", "/api/refresh")
            self.assertTrue(entered.wait(timeout=2))
            second = self._request("POST", "/api/refresh")
            release.set()
            result = self._wait_for_refresh(first.json()["operation_id"])

        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["detail"]["code"], "refresh_in_progress")
        self.assertEqual(
            second.json()["detail"]["operation_id"], first.json()["operation_id"]
        )
        self.assertEqual(result["state"], "completed")

    def test_failed_refresh_keeps_previous_projection_queryable(self) -> None:
        """Queries use the last publication during work and after a failed attempt."""
        self._write_config()
        workbook(self.source_dir / "ReportHistory.xlsx")
        entered = threading.Event()
        release = threading.Event()

        with self._paths():
            initial = self._request("POST", "/api/refresh")
            first_result = self._wait_for_refresh(initial.json()["operation_id"])
        self.assertEqual(first_result["state"], "completed")

        def failing_refresh(
            _service: object,
            _database_path: str | Path,
            **_kwargs: object,
        ) -> ImportSummary:
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test refresh was not released")
            raise ValueError("comentário corresponde a múltiplos grupos")

        with self._paths(), patch(
            "algobotdash.refresh_runner.ImportService.refresh_with_lock",
            autospec=True,
            side_effect=failing_refresh,
        ):
            attempt = self._request("POST", "/api/refresh")
            self.assertTrue(entered.wait(timeout=2))
            during = self._request("GET", "/api/positions")
            release.set()
            result = self._wait_for_refresh(attempt.json()["operation_id"])
            after = self._request("GET", "/api/positions")

        self.assertEqual(during.status_code, 200)
        self.assertEqual(after.status_code, 200)
        self.assertEqual(during.json()["total"], 1)
        self.assertEqual(after.json()["total"], 1)
        self.assertEqual(result["state"], "error")
        self.assertEqual(result["error"]["code"], "validation_error")

    def test_refresh_api_returns_not_found_for_unknown_operation(self) -> None:
        """Unknown operation IDs have a stable 404 response."""
        with self._paths():
            response = self._request("GET", "/api/refresh/unknown")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"], "refresh_operation_not_found"
        )

    def test_refresh_api_validates_local_origins_without_allowlist_bypass(self) -> None:
        """Only marked requests from exact loopback origins can start a refresh."""
        operation = RefreshOperation(
            operation_id="origin-test",
            state="queued",
            stage="queued",
            created_at="2026-09-20T12:00:00+00:00",
            started_at=None,
            finished_at=None,
            expected_revision="revision",
            summary={},
            error_code=None,
            error_message=None,
        )
        with self._paths(), patch(
            "algobotdash.web.RefreshRunner.start", return_value=operation
        ):
            no_origin = self._request(
                "POST",
                "/api/refresh",
                headers={"X-Algobotdash-Request": "refresh"},
            )
            localhost = self._request(
                "POST",
                "/api/refresh",
                headers={
                    "X-Algobotdash-Request": "refresh",
                    "Origin": "http://localhost",
                },
            )
            localhost_port = self._request(
                "POST",
                "/api/refresh",
                headers={
                    "X-Algobotdash-Request": "refresh",
                    "Origin": "https://localhost:8765",
                },
            )
            ipv4 = self._request(
                "POST",
                "/api/refresh",
                headers={
                    "X-Algobotdash-Request": "refresh",
                    "Origin": "http://127.0.0.1:8765",
                },
            )
            ipv6 = self._request(
                "POST",
                "/api/refresh",
                headers={
                    "X-Algobotdash-Request": "refresh",
                    "Origin": "http://[::1]:8765",
                },
            )
            unmarked = self._request("POST", "/api/refresh", headers={})
            lookalike = self._request(
                "POST",
                "/api/refresh",
                headers={
                    "X-Algobotdash-Request": "refresh",
                    "Origin": "https://localhost.example.com",
                },
            )
            malformed = self._request(
                "POST",
                "/api/refresh",
                headers={
                    "X-Algobotdash-Request": "refresh",
                    "Origin": "http://[::1",
                },
            )
            userinfo = self._request(
                "POST",
                "/api/refresh",
                headers={
                    "X-Algobotdash-Request": "refresh",
                    "Origin": "http://localhost@example.com",
                },
            )

        self.assertEqual(
            (
                no_origin.status_code,
                localhost.status_code,
                localhost_port.status_code,
                ipv4.status_code,
                ipv6.status_code,
            ),
            (403, 202, 202, 202, 202),
        )
        self.assertEqual(
            (
                unmarked.status_code,
                lookalike.status_code,
                malformed.status_code,
                userinfo.status_code,
            ),
            (403, 403, 403, 403),
        )
        self.assertEqual(
            lookalike.json()["detail"]["code"], "refresh_request_forbidden"
        )

    def test_refresh_api_reports_unavailable_platform_lock(self) -> None:
        """A runtime without fcntl keeps reads available and rejects refresh clearly."""
        store = RefreshOperationStore.for_database(
            self.data_dir / "algobotdash.sqlite"
        )
        operation = store.create("pending-revision")
        with self._paths(), patch("algobotdash.refresh._fcntl", None):
            status = self._request("GET", "/api/status")
            persisted = self._request(
                "GET", f"/api/refresh/{operation.operation_id}"
            )
            refresh = self._request("POST", "/api/refresh")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(
            status.json()["refresh_operation"]["operation_id"],
            operation.operation_id,
        )
        self.assertEqual(persisted.status_code, 200)
        self.assertEqual(persisted.json()["state"], "queued")
        self.assertEqual(refresh.status_code, 503)
        self.assertEqual(
            refresh.json()["detail"]["code"], "refresh_state_unavailable"
        )

    def test_fastapi_health_returns_service_error_for_missing_config(self) -> None:
        """FastAPI health should fail HTTP checks when configuration is absent."""

        async def request_health() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.get("/health")

        with self._paths():
            response = asyncio.run(request_health())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")

    def test_fastapi_health_returns_controlled_error_for_non_utf8_config(self) -> None:
        """Health should diagnose a configuration that cannot be decoded."""

        async def request_health() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.get("/health")

        (self.config_dir / "config.yaml").write_bytes(b"\xff")
        with self._paths():
            response = asyncio.run(request_health())

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["configuration"], "invalid")
        self.assertEqual(payload["status"], "error")

    def test_health_distinguishes_missing_configuration_source_and_projection(self) -> None:
        """Health should distinguish an absent configuration from other states."""
        payload = self._health_payload_with(status="error", version="0.1.0")
        self.assertEqual(payload["configuration"], "invalid")
        self.assertEqual(payload["source"], "unknown")
        self.assertEqual(payload["projection"], "unavailable")
        self.assertIn(str(self.config_dir / "config.yaml"), payload["error"])

    def test_health_reports_valid_configuration_and_source(self) -> None:
        """Health should report a valid configuration and available source."""
        self._write_config()
        (self.source_dir / "ReportHistory.xlsx").write_bytes(b"fixture")

        payload = self._health_payload_with(configuration="valid", source="available")
        self.assertEqual(payload["projection"], "unavailable")

    def test_health_reports_valid_configuration_with_missing_source(self) -> None:
        """Health should report a valid configuration and missing source."""
        self._write_config()

        payload = self._health_payload_with(configuration="valid", source="missing")
        self.assertEqual(payload["status"], "error")

    def test_health_reports_invalid_projection_when_database_is_unreadable(self) -> None:
        """Health should reject an unreadable SQLite projection."""
        self._write_config()
        database_path = self.data_dir / "algobotdash.sqlite"
        database_path.write_bytes(b"projection")

        self._health_payload_with(projection="invalid", status="error")

    def test_health_rejects_projection_with_previous_table_shape(self) -> None:
        """Health should validate the complete current schema, not only imports."""
        connection = self._open_valid_projection()
        connection.execute("DROP TABLE transactions")
        connection.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY)")
        payload = self._commit_projection_and_read_health(connection)

        self.assertEqual(payload["projection"], "invalid")
        self.assertEqual(payload["status"], "error")

    def test_health_reports_valid_projection_and_last_import(self) -> None:
        """Health should expose the latest successful import timestamp."""
        connection = self._open_valid_projection()
        connection.execute(
            "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "ReportHistory.xlsx", "hash", "2026-08-31T10:00:00+00:00", 1, 1, 0, 0),
        )
        payload = self._commit_projection_and_read_health(connection)

        self.assertEqual(payload["projection"], "available")
        self.assertEqual(payload["last_imported_at"], "2026-08-31T10:00:00+00:00")
