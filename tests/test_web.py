from __future__ import annotations

import os
import sqlite3
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from algobotdash.storage import SCHEMA
from algobotdash.web import app, dashboard, health


class WebTests(unittest.TestCase):
    tmp_path: Path = Path()
    config_dir: Path = Path()
    source_dir: Path = Path()
    data_dir: Path = Path()

    def setUp(self) -> None:
        self.tmp_path = Path(tempfile.mkdtemp(prefix="algobotdash-web-tests-"))
        self.config_dir = self.tmp_path / "config"
        self.source_dir = self.tmp_path / "source"
        self.data_dir = self.tmp_path / "data"
        self.config_dir.mkdir()
        self.source_dir.mkdir()
        self.data_dir.mkdir()
    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_path)

    def _paths(self):
        return patch.multiple(
            "algobotdash.web",
            CONFIG_PATH=self.config_dir / "config.yaml",
            DATABASE_PATH=self.data_dir / "algobotdash.sqlite",
        )

    def test_dashboard_serves_own_static_page(self) -> None:
        response = dashboard()

        self.assertEqual(response.media_type, "text/html")
        response_path = Path(response.path)
        self.assertEqual(response_path.name, "index.html")
        page = response_path.read_text(encoding="utf-8")
        self.assertIn("Dashboard local", page)
        self.assertNotIn("generate_trade_report", page)

    @unittest.skipUnless(
        os.getenv("ALGOBOTDASH_RUN_FRAMEWORK_CLIENT_TEST") == "1",
        "TestClient trava neste ambiente; execute com ALGOBOTDASH_RUN_FRAMEWORK_CLIENT_TEST=1",
    )
    def test_fastapi_serves_dashboard_over_http(self) -> None:
        with TestClient(app) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertIn("Dashboard local", response.text)

    def test_health_distinguishes_missing_configuration_source_and_projection(self) -> None:
        with self._paths():
            payload = health()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["version"], "0.1.0")
        self.assertEqual(payload["configuration"], "invalid")
        self.assertEqual(payload["source"], "unknown")
        self.assertEqual(payload["projection"], "unavailable")
        self.assertIn(str(self.config_dir / "config.yaml"), payload["error"])

    def test_health_reports_valid_configuration_and_source(self) -> None:
        config_path = self.config_dir / "config.yaml"
        config_path.write_text(
            f"source:\n  path: {self.source_dir / 'ReportHistory.xlsx'}\n",
            encoding="utf-8",
        )
        (self.source_dir / "ReportHistory.xlsx").write_bytes(b"fixture")

        with self._paths():
            payload = health()

        self.assertEqual(payload["configuration"], "valid")
        self.assertEqual(payload["source"], "available")
        self.assertEqual(payload["projection"], "unavailable")

    def test_health_reports_valid_configuration_with_missing_source(self) -> None:
        config_path = self.config_dir / "config.yaml"
        config_path.write_text(
            f"source:\n  path: {self.source_dir / 'ReportHistory.xlsx'}\n",
            encoding="utf-8",
        )

        with self._paths():
            payload = health()

        self.assertEqual(payload["configuration"], "valid")
        self.assertEqual(payload["source"], "missing")

    def test_health_reports_invalid_projection_when_database_is_unreadable(self) -> None:
        config_path = self.config_dir / "config.yaml"
        config_path.write_text(
            f"source:\n  path: {self.source_dir / 'ReportHistory.xlsx'}\n",
            encoding="utf-8",
        )
        database_path = self.data_dir / "algobotdash.sqlite"
        database_path.write_bytes(b"projection")

        with self._paths():
            payload = health()

        self.assertEqual(payload["projection"], "invalid")

    def test_health_reports_valid_projection_and_last_import(self) -> None:
        config_path = self.config_dir / "config.yaml"
        config_path.write_text(
            f"source:\n  path: {self.source_dir / 'ReportHistory.xlsx'}\n",
            encoding="utf-8",
        )
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
