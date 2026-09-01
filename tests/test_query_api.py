"""Integration tests for the read-only dashboard query API."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx

from algobotdash.storage import CURRENT_SCHEMA_VERSION, SCHEMA, read_positions
from algobotdash.web import app


class QueryApiTests(unittest.TestCase):
    """Verify externally visible position and projection query behavior."""

    def setUp(self) -> None:
        self.tmp_path = Path(tempfile.mkdtemp(prefix="algobotdash-query-tests-"))
        self.config_path = self.tmp_path / "config.yaml"
        self.database_path = self.tmp_path / "algobotdash.sqlite"
        source_path = self.tmp_path / "ReportHistory.xlsx"
        source_path.write_bytes(b"fixture")
        self.config_path.write_text(
            "\n".join(
                [
                    "source:",
                    f"  path: {source_path}",
                    "strategies:",
                    "  groups:",
                    "    - name: Turtle",
                    "      patterns: ['TurtleS2']",
                    "    - name: FVG",
                    "      patterns: ['FVGscalp']",
                ]
            ),
            encoding="utf-8",
        )
        self.addCleanup(shutil.rmtree, self.tmp_path)

    def _paths(self):
        return patch.multiple(
            "algobotdash.web",
            CONFIG_PATH=self.config_path,
            DATABASE_PATH=self.database_path,
        )

    def _request(self, path: str) -> httpx.Response:
        async def request() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.get(path)

        with self._paths():
            return asyncio.run(request())

    @contextmanager
    def _projection(self) -> Iterator[sqlite3.Connection]:
        """Create a projection fixture and always close its SQLite connection."""
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executescript(SCHEMA)
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _seed_projection(self) -> None:
        with self._projection() as connection:
            connection.executemany(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (1, "older.xlsx", "old-hash", "2026-08-30T10:00:00+00:00", 3, 2, 1, 0),
                    (
                        2, "ReportHistory.xlsx", "new-hash", "2026-08-31T10:00:00+00:00",
                        4, 3, 1, 1,
                    ),
                ],
            )
            connection.executemany(
                "INSERT INTO positions("
                "position_id, strategy, symbol_family, symbol_raw, direction, entry_at, exit_at, "
                "status, volume_requested, volume_executed, entry_price, exit_price, commission, "
                "swap, pnl, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        "100", "Turtle", "WIN", "WINQ26", "buy", "2026-08-01T10:00:00-03:00",
                        "2026-08-01T11:00:00-03:00", "closed", 1, 1, 100, 110, -1, 0, 9, 2,
                    ),
                    (
                        "200", None, "WDO", "WDOU26", "sell", "2026-08-02T10:00:00+00:00",
                        None, "open", 1, 1, 200, None, -1, 0, 999, 2,
                    ),
                    (
                        "300", "FVG", "WIN", "WINV26", "buy", "2026-08-03T10:00:00+00:00",
                        "2026-08-03T12:00:00+00:00", "closed", 1, 1, 300, 280, -1, 0, -21, 2,
                    ),
                ],
            )
            connection.execute(
                "INSERT INTO orders("
                "order_id, position_id, strategy, symbol_raw, direction, opened_at, event_at, "
                "status, volume_requested, volume_executed, price, stop_loss, take_profit, "
                "comment, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "500", "100", "Turtle", "WINQ26", "buy", "2026-08-01T09:59:00-03:00",
                    "2026-08-01T10:00:00-03:00", "filled", 1, 1, 100, None, None,
                    "TurtleS2", 2,
                ),
            )

    def test_positions_are_paginated_sorted_and_preserve_unknown_strategy(self) -> None:
        """List positions by a public sort and preserve an unproven strategy."""
        self._seed_projection()

        response = self._request(
            "/api/positions?limit=2&sort_by=opened_at&sort_order=desc"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(
            [item["position_id"] for item in payload["items"]], ["300", "200"]
        )
        self.assertIsNone(payload["items"][1]["strategy"])
        self.assertIsNone(payload["items"][1]["realized_pnl"])

        earliest = self._request("/api/positions?limit=1&sort_by=opened_at&sort_order=asc")
        self.assertEqual(earliest.json()["items"][0]["opened_at"], "2026-08-01T13:00:00+00:00")

        default_order = self._request("/api/positions")
        self.assertEqual(
            [item["position_id"] for item in default_order.json()["items"]],
            ["300", "100", "200"],
        )

    def test_positions_reject_invalid_pagination_and_sorting(self) -> None:
        """Reject values outside the documented page and ordering contract."""
        self._seed_projection()

        self.assertEqual(self._request("/api/positions?limit=201").status_code, 422)
        self.assertEqual(self._request("/api/positions?offset=-1").status_code, 422)
        self.assertEqual(self._request("/api/positions?sort_by=comment").status_code, 422)
        self.assertEqual(self._request("/api/positions?sort_order=sideways").status_code, 422)

    def test_storage_rejects_sort_tokens_outside_allowlists(self) -> None:
        """Keep SQL ordering tokens restricted even outside the validated HTTP API."""
        self._seed_projection()

        with self.assertRaisesRegex(ValueError, "ordenação de posições inválida"):
            _ = read_positions(
                self.database_path,
                limit=10,
                offset=0,
                sort_by="opened_at",
                sort_order="DESC; DROP TABLE positions",
            )

    def test_realized_pnl_sort_uses_the_exposed_realized_value(self) -> None:
        """Keep open positions without realized P&L after realized positions."""
        self._seed_projection()

        response = self._request("/api/positions?sort_by=realized_pnl&sort_order=desc")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["position_id"] for item in response.json()["items"]],
            ["100", "300", "200"],
        )

    def test_position_orders_use_report_identifier_and_distinguish_missing_position(self) -> None:
        """Use stable report IDs and distinguish no orders from no position."""
        self._seed_projection()

        details = self._request("/api/positions/100/orders")
        no_orders = self._request("/api/positions/200/orders")
        missing = self._request("/api/positions/999/orders")

        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.json()["items"][0]["order_id"], "500")
        self.assertEqual(no_orders.status_code, 200)
        self.assertEqual(no_orders.json(), {"items": []})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"]["code"], "position_not_found")
        self.assertEqual(details.json()["items"][0]["opened_at"], "2026-08-01T12:59:00+00:00")

    def test_strategies_imports_and_status_expose_read_contract(self) -> None:
        """Expose configuration, valid history, and the current projection state."""
        self._seed_projection()
        strategies = self._request("/api/strategies")
        imports = self._request("/api/imports?limit=1")
        status = self._request("/api/status")

        self.assertEqual(strategies.status_code, 200)
        self.assertEqual(
            strategies.json(), {"items": [{"name": "Turtle"}, {"name": "FVG"}]}
        )
        self.assertEqual(imports.status_code, 200)
        self.assertEqual(imports.json()["total"], 2)
        self.assertEqual(imports.json()["items"][0]["source_hash"], "new-hash")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["state"], "ready")
        self.assertEqual(status.json()["source_name"], "ReportHistory.xlsx")
        self.assertEqual(status.json()["last_import"]["source_hash"], "new-hash")

    def test_status_is_empty_without_import_and_queries_report_unavailable_projection(self) -> None:
        """Keep status readable while rejecting queries without a projection."""
        with self._projection():
            pass

        empty_status = self._request("/api/status")
        self.assertEqual(empty_status.status_code, 200)
        self.assertEqual(empty_status.json()["state"], "empty")
        self.assertIsNone(empty_status.json()["last_import"])

        self.database_path.unlink()
        unavailable = self._request("/api/positions")
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["detail"]["code"], "projection_unavailable")

    def test_status_marks_partial_projection_as_unavailable(self) -> None:
        """Reject a database that lacks a table required by query endpoints."""
        with self._projection() as connection:
            connection.execute("DROP TABLE transactions")
            connection.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY)")

        status = self._request("/api/status")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["state"], "unavailable")
        self.assertEqual(status.json()["projection"], "invalid")

    def test_positions_reject_duplicate_schema_version_rows(self) -> None:
        """Reject projections whose schema version has more than one row."""
        connection = sqlite3.connect(self.database_path)
        connection.executescript(SCHEMA)
        connection.execute("DROP TABLE schema_version")
        connection.execute("CREATE TABLE schema_version (version INTEGER)")
        connection.executemany(
            "INSERT INTO schema_version(version) VALUES (?)",
            [(CURRENT_SCHEMA_VERSION,), (CURRENT_SCHEMA_VERSION,)],
        )
        connection.commit()
        connection.close()

        response = self._request("/api/positions")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "projection_unavailable")

    def test_positions_sort_subsecond_timestamps_chronologically(self) -> None:
        """Keep timestamp ordering precise below whole-second resolution."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 2, 2, 0, 0),
            )
            connection.executemany(
                "INSERT INTO positions("
                "position_id, strategy, symbol_family, symbol_raw, direction, entry_at, exit_at, "
                "status, volume_requested, volume_executed, entry_price, exit_price, commission, "
                "swap, pnl, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        "a-later", None, None, "WINQ26", "buy",
                        "2026-08-01T10:00:00.000400-03:00", None, "open", None, None,
                        None, None, 0, 0, 0, 1,
                    ),
                    (
                        "z-earlier", None, None, "WINQ26", "buy",
                        "2026-08-01T10:00:00.000100-03:00", None, "open", None, None,
                        None, None, 0, 0, 0, 1,
                    ),
                ],
            )

        response = self._request("/api/positions?sort_by=opened_at&sort_order=asc")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["position_id"] for item in response.json()["items"]],
            ["z-earlier", "a-later"],
        )

    def test_positions_reject_timezone_naive_timestamp(self) -> None:
        """Reject a projection whose position timestamp has no timezone offset."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 1, 1, 0, 0),
            )
            connection.execute(
                "INSERT INTO positions("
                "position_id, strategy, symbol_family, symbol_raw, direction, entry_at, exit_at, "
                "status, volume_requested, volume_executed, entry_price, exit_price, commission, "
                "swap, pnl, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "naive", None, None, "WINQ26", "buy", "2026-08-01T10:00:00", None,
                    "open", None, None, None, None, 0, 0, 0, 1,
                ),
            )

        response = self._request("/api/positions?sort_by=opened_at")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "projection_unavailable")

    def test_imports_reject_malformed_timestamp(self) -> None:
        """Reject a projection whose valid-import history contains an invalid timestamp."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "not-a-timestamp", 1, 1, 0, 0),
            )

        response = self._request("/api/imports")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "projection_unavailable")

    def test_imports_sort_by_utc_timestamp_before_identifier(self) -> None:
        """Order imports by their UTC instant rather than local timestamp text."""
        with self._projection() as connection:
            connection.executemany(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (1, "later.xlsx", "later", "2026-08-01T10:00:00-03:00", 1, 1, 0, 0),
                    (2, "earlier.xlsx", "earlier", "2026-08-01T11:00:00+00:00", 1, 1, 0, 0),
                ],
            )

        response = self._request("/api/imports")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["items"]], [1, 2])
