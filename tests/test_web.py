"""Tests for dashboard endpoints and health diagnostics."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import subprocess  # nosec B404 -- required to execute the fixed Node.js test harness
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from algobotdash.storage import SCHEMA
from algobotdash.web import app, dashboard, health

NODE_EXECUTABLE = shutil.which("node")


class WebTests(unittest.TestCase):
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
            f"source:\n  path: {self.source_dir / 'ReportHistory.xlsx'}\n",
            encoding="utf-8",
        )

    def _health_payload(self) -> dict[str, Any]:
        """Read health state using the isolated test paths."""
        with self._paths():
            return health()

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
        self.assertIn('fetch("/api/filter-options"', content)
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
        self.assertIn('id="metrics-state" role="alert"', content)
        self.assertIn('id="metric-net-pnl"', content)
        self.assertIn('id="metric-gross-profit"', content)
        self.assertIn('id="metric-gross-loss"', content)
        self.assertIn('id="metric-win-rate"', content)
        self.assertIn('id="metric-profit-factor"', content)
        self.assertIn('id="metric-payoff"', content)
        self.assertIn('id="metric-expectancy"', content)
        self.assertIn('id="metric-position-sharpe"', content)
        self.assertIn('id="metric-position-sortino"', content)
        self.assertIn('id="metric-total-sample"', content)
        self.assertIn('id="metric-excluded-open"', content)
        self.assertIn("Projeção indisponível.", content)

    @unittest.skipUnless(NODE_EXECUTABLE, "requires Node.js for JavaScript execution")
    def test_dashboard_invalidates_pending_queries_and_validates_filters(self) -> None:
        """Terminal status and invalid filters must not leak stale dashboard state."""
        runner = r"""
const fs = require("fs");
const vm = require("vm");
const html = fs.readFileSync(process.env.DASHBOARD_PATH, "utf8");
const source = html.match(/<script>([\s\S]*)<\/script>/)[1];
class Element {
  constructor() { this.value = ""; this.listeners = {}; this.children = []; }
  replaceChildren(...children) { this.children = children; }
  append(child) { this.children.push(child); }
  addEventListener(name, handler) { this.listeners[name] = handler; }
}
const ids = ["service", "configuration", "source", "projection", "source-name",
  "source-hash", "last-imported-at", "updated-at", "error", "filter-state", "table-state",
  "metrics-state", "metric-net-pnl", "metric-gross-profit", "metric-gross-loss",
  "metric-win-rate", "metric-profit-factor", "metric-payoff", "metric-expectancy",
  "metric-position-sharpe", "metric-position-sortino", "metric-total-sample",
  "metric-excluded-open", "positions-body", "page-summary", "previous-page", "next-page",
  "sort-by", "sort-order", "filter-strategy", "filter-symbol-family", "filter-direction",
  "filter-status", "filter-association", "date-from", "date-to"];
const elements = Object.fromEntries(ids.map((id) => ["#" + id, new Element()]));
elements["#sort-by"].value = "closed_at";
elements["#sort-order"].value = "desc";
elements["#filter-status"].value = "closed";
elements["#filter-association"].value = "all";
let interval;
let state = "ready";
let positionFetches = 0;
let positionMode = "normal";
let filterMode = "normal";
const pendingPositions = [];
const pendingFilters = [];
const statusPayload = () => ({state, configuration: "valid", source: "available",
  projection: state === "unavailable" ? "invalid" : "available", source_name: "Report.xlsx",
  last_import: state === "ready" ? {source_hash: "hash", imported_at: "2026-08-01T00:00:00Z"} : null});
const context = {
  document: {querySelector: (selector) => elements[selector], createElement: () => new Element()},
  fetch: async (url) => {
    if (url === "/api/status") return {ok: true, status: 200, json: async () => statusPayload()};
    if (url === "/api/filter-options") {
      if (filterMode === "pending") return new Promise((resolve) => pendingFilters.push(resolve));
      return {ok: true, status: 200, json: async () => ({strategies: ["Turtle"], symbol_families: ["WIN"]})};
    }
    if (url.startsWith("/api/metrics")) {
      return {ok: true, status: 200, json: async () => ({
        total_sample: 0, excluded_open_count: 0, net_pnl: null, gross_profit: null, gross_loss: null,
        win_rate: null, profit_factor: null, payoff: null, expectancy: null, position_sharpe: null,
        position_sortino: null, unavailable_reasons: {},
      })};
    }
    positionFetches += 1;
    if (positionMode === "pending") return new Promise((resolve) => pendingPositions.push(resolve));
    if (positionMode === "validation") return {ok: false, status: 422,
      json: async () => ({detail: {code: "contradictory_filters"}})};
    return {ok: true, status: 200, json: async () => ({items: [], total: 0})};
  },
  setInterval: (callback) => { interval = callback; return 1; },
  URLSearchParams, Intl, Date, console,
};
vm.runInNewContext(source + "\nglobalThis.loadPositions = loadPositions; globalThis.loadFilterOptions = loadFilterOptions;", context);
const flush = () => new Promise((resolve) => setImmediate(resolve));
const watchdog = setTimeout(() => {
  console.error("dashboard regression test did not complete");
  process.exitCode = 1;
}, 2000);
(async () => {
  await flush(); await flush();

  positionMode = "pending";
  context.loadPositions();
  filterMode = "pending";
  context.loadFilterOptions();
  state = "unavailable";
  await interval();
  pendingPositions[0]({ok: true, status: 200, json: async () => ({items: [{position_id: "stale",
    strategy: "Turtle", association: "associated", symbol_family: "WIN", direction: "buy",
    opened_at: null, closed_at: null, status: "closed", realized_pnl: 1}], total: 1})});
  pendingFilters[0]({ok: true, status: 200, json: async () => ({strategies: ["Stale"], symbol_families: ["OLD"]})});
  await flush(); await flush();
  if (elements["#page-summary"].textContent !== "Projeção indisponível.") throw new Error("stale position escaped terminal state");
  if (elements["#filter-strategy"].children.some((option) => option.value === "Stale")) throw new Error("stale filter catalog escaped terminal state");

  positionMode = "normal";
  elements["#date-from"].value = "2026-08-03";
  elements["#date-to"].value = "2026-08-01";
  const beforeInvalidDates = positionFetches;
  elements["#date-from"].listeners.change();
  await flush();
  if (positionFetches !== beforeInvalidDates) throw new Error("invalid dates reached API");
  if (!elements["#table-state"].textContent.includes("data inicial")) throw new Error("missing date validation detail");

  elements["#date-from"].value = "";
  elements["#date-to"].value = "";
  elements["#filter-strategy"].value = "Turtle";
  elements["#filter-association"].value = "unassociated";
  elements["#filter-association"].listeners.change();
  await flush();
  if (elements["#filter-strategy"].value !== "") throw new Error("contradictory strategy was not cleared");

  elements["#filter-strategy"].value = "Turtle";
  elements["#filter-strategy"].listeners.change();
  await flush();
  if (elements["#filter-association"].value !== "all") throw new Error("association was not normalized");

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
const source = html.match(/<script>([\s\S]*)<\/script>/)[1];
class Element {
  constructor() { this.value = ""; this.listeners = {}; }
  replaceChildren(...children) { this.children = children; }
  append(child) { (this.children ||= []).push(child); }
  addEventListener(name, handler) { this.listeners[name] = handler; }
}
const ids = ["service", "configuration", "source", "projection", "source-name",
  "source-hash", "last-imported-at", "updated-at", "error", "filter-state", "table-state",
  "metrics-state", "metric-net-pnl", "metric-gross-profit", "metric-gross-loss",
  "metric-win-rate", "metric-profit-factor", "metric-payoff", "metric-expectancy",
  "metric-position-sharpe", "metric-position-sortino", "metric-total-sample",
  "metric-excluded-open", "positions-body", "page-summary", "previous-page", "next-page",
  "sort-by", "sort-order", "filter-strategy", "filter-symbol-family", "filter-direction",
  "filter-status", "filter-association", "date-from", "date-to"];
const elements = Object.fromEntries(ids.map((id) => ["#" + id, new Element()]));
elements["#sort-by"].value = "closed_at";
elements["#sort-order"].value = "desc";
elements["#filter-status"].value = "closed";
elements["#filter-association"].value = "all";
let state = "ready";
let interval;
let positionFetches = 0;
let filterFetches = 0;
let filterPayload = {strategies: ["FVG", "Turtle"], symbol_families: ["WDO", "WIN"]};
let filterMode = "normal";
let lastPositionUrl = "";
let mode = "retry";
const pending = [];
let statusMode = "normal";
const pendingStatuses = [];
const payload = () => ({state, configuration: "valid", source: "available",
  projection: state === "unavailable" ? "invalid" : "available", source_name: "Report.xlsx",
  last_import: state === "ready" ? {source_hash: "same-hash", imported_at: "2026-08-01T00:00:00+00:00"} : null});
const context = {
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
    if (url.startsWith("/api/metrics")) {
      return {ok: true, json: async () => ({
        total_sample: 0, excluded_open_count: 0, net_pnl: null, gross_profit: null, gross_loss: null,
        win_rate: null, profit_factor: null, payoff: null, expectancy: null, position_sharpe: null,
        position_sortino: null, unavailable_reasons: {},
      })};
    }
    positionFetches += 1;
    lastPositionUrl = url;
    if (mode === "retry" && positionFetches === 1) return {ok: false, json: async () => ({})};
    if (mode === "failure") return {ok: false, json: async () => ({})};
    if (mode === "race") return new Promise((resolve) => pending.push(resolve));
    return {ok: true, json: async () => ({items: [], total: 120})};
  },
  setInterval: (callback) => { interval = callback; return 1; },
  URLSearchParams, Intl, Date, console,
};
vm.runInNewContext(source + "\nglobalThis.loadStatus = loadStatus;", context);
const flush = () => new Promise((resolve) => setImmediate(resolve));
(async () => {
  await flush(); await flush();
  if (filterFetches !== 1) throw new Error(`expected initial filter catalog, got ${filterFetches}`);
  if (!lastPositionUrl.includes("status=closed")) throw new Error(`missing default closed filter: ${lastPositionUrl}`);
  await interval();
  if (positionFetches !== 2) throw new Error(`expected retry after failed load, got ${positionFetches}`);
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
  elements["#next-page"].listeners.click();
  await flush();
  if (!lastPositionUrl.includes("offset=50")) throw new Error(`pagination did not advance: ${lastPositionUrl}`);
  elements["#filter-strategy"].value = "FVG";
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
  if (elements["#table-state"].hidden !== true || elements["#table-state"].textContent !== "") throw new Error("stale unavailable notice");
  mode = "failure";
  elements["#sort-by"].listeners.change();
  await flush();
  mode = "normal";
  await interval();
  if (positionFetches !== 8) throw new Error(`expected retry after active position failure, got ${positionFetches}`);
  if (elements["#filter-strategy"].value !== "FVG") throw new Error("valid strategy selection was lost");
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
  elements["#filter-strategy"].value = "Gone";
  filterPayload = {strategies: ["Turtle"], symbol_families: ["WIN"]};
  const olderStatus = context.loadStatus();
  const newerStatus = context.loadStatus();
  if (pendingStatuses.length !== 2) throw new Error(`expected 2 pending status requests, got ${pendingStatuses.length}`);
  pendingStatuses[1]({ok: true, json: async () => ({state: "ready", configuration: "valid", source: "available", projection: "available", source_name: "Report.xlsx", last_import: {source_hash: "new-hash", imported_at: "2026-08-02T00:00:00+00:00"}})});
  await flush();
  pendingStatuses[0]({ok: true, json: async () => ({state: "unavailable", configuration: "valid", source: "available", projection: "invalid", source_name: "Report.xlsx", last_import: null})});
  await Promise.all([olderStatus, newerStatus]);
  if (elements["#service"].textContent !== "pronto") throw new Error("stale status overwrote current state");
  if (elements["#filter-strategy"].value !== "") throw new Error("removed strategy selection was preserved");
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
        payload = self._health_payload()

        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["version"], "0.1.0")
        self.assertEqual(payload["configuration"], "invalid")
        self.assertEqual(payload["source"], "unknown")
        self.assertEqual(payload["projection"], "unavailable")
        self.assertIn(str(self.config_dir / "config.yaml"), payload["error"])

    def test_health_reports_valid_configuration_and_source(self) -> None:
        """Health should report a valid configuration and available source."""
        self._write_config()
        (self.source_dir / "ReportHistory.xlsx").write_bytes(b"fixture")

        payload = self._health_payload()

        self.assertEqual(payload["configuration"], "valid")
        self.assertEqual(payload["source"], "available")
        self.assertEqual(payload["projection"], "unavailable")

    def test_health_reports_valid_configuration_with_missing_source(self) -> None:
        """Health should report a valid configuration and missing source."""
        self._write_config()

        payload = self._health_payload()

        self.assertEqual(payload["configuration"], "valid")
        self.assertEqual(payload["source"], "missing")
        self.assertEqual(payload["status"], "error")

    def test_health_reports_invalid_projection_when_database_is_unreadable(self) -> None:
        """Health should reject an unreadable SQLite projection."""
        self._write_config()
        database_path = self.data_dir / "algobotdash.sqlite"
        database_path.write_bytes(b"projection")

        payload = self._health_payload()

        self.assertEqual(payload["projection"], "invalid")
        self.assertEqual(payload["status"], "error")

    def test_health_rejects_projection_with_previous_table_shape(self) -> None:
        """Health should validate the complete current schema, not only imports."""
        self._write_config()
        database_path = self.data_dir / "algobotdash.sqlite"
        connection = sqlite3.connect(database_path)
        connection.executescript(SCHEMA)
        connection.execute("DROP TABLE transactions")
        connection.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY)")
        connection.commit()
        connection.close()

        payload = self._health_payload()

        self.assertEqual(payload["projection"], "invalid")
        self.assertEqual(payload["status"], "error")

    def test_health_reports_valid_projection_and_last_import(self) -> None:
        """Health should expose the latest successful import timestamp."""
        self._write_config()
        database_path = self.data_dir / "algobotdash.sqlite"
        connection = sqlite3.connect(database_path)
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "ReportHistory.xlsx", "hash", "2026-08-31T10:00:00+00:00", 1, 1, 0, 0),
        )
        connection.commit()
        connection.close()

        payload = self._health_payload()

        self.assertEqual(payload["projection"], "available")
        self.assertEqual(payload["last_imported_at"], "2026-08-31T10:00:00+00:00")
