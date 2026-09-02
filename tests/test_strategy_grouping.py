"""Integration tests for symbol-qualified strategy identities."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from algobotdash.storage import SCHEMA
from algobotdash.web import app, dashboard
from tests.fixture_helpers import get_asgi, insert_positions


class StrategyGroupingTests(unittest.TestCase):
    """Verify strategy groups remain distinct across symbol families."""

    tmp_path: Path = Path()
    config_path: Path = Path()
    database_path: Path = Path()

    def setUp(self) -> None:
        self.tmp_path = Path(tempfile.mkdtemp(prefix="algobotdash-strategy-tests-"))
        self.config_path = self.tmp_path / "config.yaml"
        self.database_path = self.tmp_path / "algobotdash.sqlite"
        self.config_path.write_text(
            "source:\n  path: ReportHistory.xlsx\nstrategies:\n  groups:\n"
            "    - name: FVG\n      patterns: ['fvg']\n",
            encoding="utf-8",
        )
        self.addCleanup(shutil.rmtree, self.tmp_path)

    def _request(self, path: str):
        with patch.multiple(
            "algobotdash.web",
            CONFIG_PATH=self.config_path,
            DATABASE_PATH=self.database_path,
        ):
            return get_asgi(app, path)

    def _seed_projection(self) -> None:
        connection = sqlite3.connect(self.database_path)
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 3, 3, 1, 0),
        )
        insert_positions(
            connection,
            [
                (
                    "win", "FVG", "WIN", "WINQ26", "buy", "2026-08-01T10:00:00+00:00",
                    "2026-08-01T11:00:00+00:00", "closed", 1, 1, 100, 101, -1, 0, 9, 1, 1,
                ),
                (
                    "wdo", "FVG", "WDO", "WDOU26", "buy", "2026-08-02T10:00:00+00:00",
                    "2026-08-02T11:00:00+00:00", "closed", 1, 1, 5000, 5001, -1, 0, 9, 1, 1,
                ),
                (
                    "unknown", None, "WIN", "WINQ26", "buy", "2026-08-03T10:00:00+00:00",
                    None, "open", 1, 1, 100, None, -1, 0, 0, 0, 1,
                ),
            ],
        )
        connection.commit()
        connection.close()

    def test_positions_and_catalog_use_symbol_qualified_strategy_keys(self) -> None:
        """Expose WIN FVG and WDO FVG as distinct analytical identities."""
        self._seed_projection()

        positions = self._request("/api/positions?status=all&limit=10")
        configured_groups = self._request("/api/strategies")
        strategy_keys = self._request("/api/strategy-keys")

        self.assertEqual(positions.status_code, 200)
        by_id = {item["position_id"]: item for item in positions.json()["items"]}
        self.assertEqual(by_id["win"]["strategy_key"], "WIN FVG")
        self.assertEqual(by_id["wdo"]["strategy_key"], "WDO FVG")
        self.assertIsNone(by_id["unknown"]["strategy_key"])
        self.assertEqual(configured_groups.json(), {"items": [{"name": "FVG"}]})
        self.assertEqual(
            strategy_keys.json(),
            {
                "items": [
                    {
                        "strategy_key": "WDO FVG",
                        "strategy": "FVG",
                        "symbol_family": "WDO",
                    },
                    {
                        "strategy_key": "WIN FVG",
                        "strategy": "FVG",
                        "symbol_family": "WIN",
                    },
                ]
            },
        )

    def test_configured_groups_remain_available_without_projection(self) -> None:
        """Keep the YAML catalog available before an import creates strategy keys."""
        configured_groups = self._request("/api/strategies")
        strategy_keys = self._request("/api/strategy-keys")

        self.assertEqual(configured_groups.status_code, 200)
        self.assertEqual(configured_groups.json(), {"items": [{"name": "FVG"}]})
        self.assertEqual(strategy_keys.status_code, 503)
        self.assertEqual(strategy_keys.json()["detail"]["code"], "projection_unavailable")

    def test_dashboard_displays_strategy_name(self) -> None:
        """Display the strategy name while keeping strategy_key in the API contract."""
        content = Path(dashboard().path).read_text(encoding="utf-8")

        self.assertIn("<th>ID</th><th>Estratégia</th>", content)
        self.assertIn('position.association === "associated"', content)
        self.assertIn('"Sem estratégia"', content)
        self.assertIn('"Não associada"', content)
        self.assertNotIn("Estratégia analítica", content)
        self.assertNotIn("position.strategy_key ||", content)
