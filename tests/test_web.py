"""Tests for dashboard endpoints and health diagnostics."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from algobotdash.storage import SCHEMA
from algobotdash.web import app, dashboard, health


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

    def test_dashboard_serves_own_static_page(self) -> None:
        """Dashboard should serve its own static HTML page."""
        response = dashboard()

        self.assertEqual(response.media_type, "text/html")
        response_path = Path(response.path)
        self.assertEqual(response_path.name, "index.html")
        page = response_path.read_text(encoding="utf-8")
        self.assertIn("Dashboard local", page)
        self.assertNotIn("generate_trade_report", page)

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

    def test_health_distinguishes_missing_configuration_source_and_projection(self) -> None:
        """Health should distinguish an absent configuration from other states."""
        with self._paths():
            payload = health()

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

        with self._paths():
            payload = health()

        self.assertEqual(payload["configuration"], "valid")
        self.assertEqual(payload["source"], "available")
        self.assertEqual(payload["projection"], "unavailable")

    def test_health_reports_valid_configuration_with_missing_source(self) -> None:
        """Health should report a valid configuration and missing source."""
        self._write_config()

        with self._paths():
            payload = health()

        self.assertEqual(payload["configuration"], "valid")
        self.assertEqual(payload["source"], "missing")
        self.assertEqual(payload["status"], "error")

    def test_health_reports_invalid_projection_when_database_is_unreadable(self) -> None:
        """Health should reject an unreadable SQLite projection."""
        self._write_config()
        database_path = self.data_dir / "algobotdash.sqlite"
        database_path.write_bytes(b"projection")

        with self._paths():
            payload = health()

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

        with self._paths():
            payload = health()

        self.assertEqual(payload["projection"], "available")
        self.assertEqual(payload["last_imported_at"], "2026-08-31T10:00:00+00:00")
