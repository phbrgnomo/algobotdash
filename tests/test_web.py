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
        self.assertIn("/api/positions?${query}", content)
        self.assertIn('id="positions-body"', content)
        self.assertIn('id="previous-page"', content)
        self.assertIn('id="next-page"', content)
        self.assertIn("Projeção indisponível.", content)

    @unittest.skipUnless(NODE_EXECUTABLE, "requires Node.js for JavaScript execution")
    def test_dashboard_recovers_positions_when_projection_returns_with_same_hash(self) -> None:
        """Reload positions after unavailable state even when source hash is unchanged."""
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
  "source-hash", "last-imported-at", "updated-at", "error", "table-state",
  "positions-body", "page-summary", "previous-page", "next-page", "sort-by", "sort-order"];
const elements = Object.fromEntries(ids.map((id) => ["#" + id, new Element()]));
elements["#sort-by"].value = "closed_at";
elements["#sort-order"].value = "desc";
let state = "ready";
let interval;
let positionFetches = 0;
let mode = "retry";
const pending = [];
const payload = () => ({state, configuration: "valid", source: "available",
  projection: state === "unavailable" ? "invalid" : "available", source_name: "Report.xlsx",
  last_import: state === "ready" ? {source_hash: "same-hash", imported_at: "2026-08-01T00:00:00+00:00"} : null});
const context = {
  document: {querySelector: (selector) => elements[selector], createElement: () => new Element()},
  fetch: async (url) => {
    if (url === "/api/status") return {ok: true, json: async () => payload()};
    positionFetches += 1;
    if (mode === "retry" && positionFetches === 1) return {ok: false, json: async () => ({})};
    if (mode === "race") return new Promise((resolve) => pending.push(resolve));
    return {ok: true, json: async () => ({items: [], total: 0})};
  },
  setInterval: (callback) => { interval = callback; return 1; },
  URLSearchParams, Intl, Date, console,
};
vm.runInNewContext(source, context);
const flush = () => new Promise((resolve) => setImmediate(resolve));
(async () => {
  await flush(); await flush();
  await interval();
  if (positionFetches !== 2) throw new Error(`expected retry after failed load, got ${positionFetches}`);
  state = "unavailable";
  await interval();
  if (elements["#page-summary"].textContent !== "Projeção indisponível.") throw new Error("missing unavailable table state");
  if (elements["#table-state"].hidden || !elements["#table-state"].textContent.includes("projeção SQLite está indisponível")) throw new Error("missing unavailable error notice");
  if (!elements["#previous-page"].disabled || !elements["#next-page"].disabled) throw new Error("pagination remains enabled");
  state = "ready";
  await interval();
  if (positionFetches !== 3) throw new Error(`expected recovery fetch, got ${positionFetches}`);
  if (elements["#table-state"].hidden !== true || elements["#table-state"].textContent !== "") throw new Error("stale unavailable notice");
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
