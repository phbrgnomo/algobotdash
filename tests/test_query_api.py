"""Integration tests for the read-only dashboard query API."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from algobotdash.storage import SCHEMA, read_positions
from algobotdash.web import app
from tests.fixture_helpers import get_asgi, insert_positions


class QueryApiTests(unittest.TestCase):
    """Verify externally visible position and projection query behavior."""

    tmp_path: Path = Path()
    config_path: Path = Path()
    database_path: Path = Path()

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

    def _request(self, path: str):
        with self._paths():
            return get_asgi(app, path)

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
            insert_positions(
                connection,
                [
                    (
                        "100", "Turtle", "WIN", "WINQ26", "buy", "2026-08-01T10:00:00-03:00",
                        "2026-08-01T11:00:00-03:00", "closed", 1, 1, 100, 110, -1, 0, 9, 1, 2,
                    ),
                    (
                        "200", None, "WDO", "WDOU26", "sell", "2026-08-02T10:00:00+00:00",
                        None, "open", 1, 1, 200, None, -1, 0, 999, 0, 2,
                    ),
                    (
                        "300", "FVG", "WIN", "WINV26", "buy", "2026-08-03T10:00:00+00:00",
                        "2026-08-03T12:00:00+00:00", "closed", 1, 1, 300, 280, -1, 0, -21, 1, 2,
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
            "/api/positions?status=all&limit=2&sort_by=opened_at&sort_order=desc"
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

        earliest = self._request(
            "/api/positions?status=all&limit=1&sort_by=opened_at&sort_order=asc"
        )
        self.assertEqual(earliest.json()["items"][0]["opened_at"], "2026-08-01T13:00:00+00:00")

        default_order = self._request("/api/positions")
        self.assertEqual(
            [item["position_id"] for item in default_order.json()["items"]],
            ["300", "100"],
        )

    def test_positions_apply_shared_filters_and_report_association(self) -> None:
        """Combine analytical dimensions and expose proven association state."""
        self._seed_projection()

        response = self._request(
            "/api/positions?strategy=Turtle&symbol_family=WIN&direction=buy"
            "&association=associated&date_from=2026-08-01&date_to=2026-08-01"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["position_id"], "100")
        self.assertEqual(response.json()["items"][0]["association"], "associated")

        unassociated = self._request(
            "/api/positions?status=open&association=unassociated"
        )
        self.assertEqual(unassociated.status_code, 200)
        self.assertEqual(
            [item["position_id"] for item in unassociated.json()["items"]], ["200"]
        )
        self.assertEqual(unassociated.json()["items"][0]["association"], "unassociated")

    def test_each_shared_filter_is_independently_effective(self) -> None:
        """Prove every dimension against rows that differ on the other dimensions."""
        self._seed_projection()

        cases = {
            "strategy": ("/api/positions?status=all&strategy=Turtle", ["100"]),
            "strategy_outer_whitespace": (
                "/api/positions?status=all&strategy=%20Turtle%20", ["100"]
            ),
            "strategy_case_sensitive": (
                "/api/positions?status=all&strategy=turtle", []
            ),
            "symbol_family": ("/api/positions?status=all&symbol_family=WDO", ["200"]),
            "symbol_family_missing": (
                "/api/positions?status=all&symbol_family=IND", []
            ),
            "direction": ("/api/positions?status=all&direction=sell", ["200"]),
            "associated": (
                "/api/positions?status=all&association=associated", ["300", "100"]
            ),
            "unassociated": (
                "/api/positions?status=all&association=unassociated", ["200"]
            ),
        }
        for name, (path, expected) in cases.items():
            with self.subTest(name=name):
                response = self._request(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [item["position_id"] for item in response.json()["items"]],
                    expected,
                )

    def test_position_dates_use_bahia_day_and_status_specific_timestamp(self) -> None:
        """Use exit day for closed positions and entry day for open positions."""
        self._seed_projection()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE positions SET exit_at = ? WHERE position_id = '100'",
                ("2026-08-02T01:30:00+00:00",),
            )
            connection.execute(
                "UPDATE positions SET entry_at = ? WHERE position_id = '200'",
                ("2026-08-03T01:30:00+00:00",),
            )

        closed = self._request(
            "/api/positions?date_from=2026-08-01&date_to=2026-08-01"
        )
        opened = self._request(
            "/api/positions?status=open&date_from=2026-08-02&date_to=2026-08-02"
        )

        self.assertEqual(
            [item["position_id"] for item in closed.json()["items"]], ["100"]
        )
        self.assertEqual(
            [item["position_id"] for item in opened.json()["items"]], ["200"]
        )

    def test_positions_validate_filter_contract(self) -> None:
        """Reject invalid ranges, blank dimensions, and contradictory filters."""
        self._seed_projection()

        inverted = self._request(
            "/api/positions?date_from=2026-08-03&date_to=2026-08-01"
        )
        contradictory = self._request(
            "/api/positions?strategy=Turtle&association=unassociated"
        )
        blank = self._request("/api/positions?strategy=%20%20")
        unknown = self._request("/api/positions?strategy=Missing")
        injection = self._request(
            "/api/positions?status=all&strategy=Turtle%27%20OR%201%3D1%20--"
        )
        invalid_enum_paths = (
            "/api/positions?direction=sideways",
            "/api/positions?status=finished",
            "/api/positions?association=maybe",
        )

        self.assertEqual(inverted.status_code, 422)
        self.assertEqual(inverted.json()["detail"]["code"], "invalid_date_range")
        self.assertEqual(contradictory.status_code, 422)
        self.assertEqual(
            contradictory.json()["detail"]["code"], "contradictory_filters"
        )
        self.assertEqual(blank.status_code, 422)
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(unknown.json()["total"], 0)
        self.assertEqual(injection.status_code, 200)
        self.assertEqual(injection.json()["total"], 0)
        for path in invalid_enum_paths:
            with self.subTest(path=path):
                self.assertEqual(self._request(path).status_code, 422)

    def test_filter_options_are_observed_distinct_and_sorted(self) -> None:
        """Return one deterministic catalog built from the current projection."""
        self._seed_projection()
        with sqlite3.connect(self.database_path) as connection:
            insert_positions(
                connection,
                [
                    (
                        "400", None, "BIT", "BITQ26", "sell",
                        "2026-08-04T10:00:00-03:00",
                        "2026-08-04T11:00:00-03:00", "closed", 1, 1, 1, 2,
                        0, 0, 1, 0, 2,
                    )
                ],
            )

        response = self._request("/api/filter-options")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "strategies": ["FVG", "Turtle"],
                "symbol_families": ["BIT", "WDO", "WIN"],
            },
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

        response = self._request(
            "/api/positions?status=all&sort_by=realized_pnl&sort_order=desc"
        )

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

    def test_projection_uses_one_unversioned_schema(self) -> None:
        """The MVP projection should have no schema-version metadata."""
        with self._projection() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

        self.assertNotIn("schema_version", tables)
        self.assertEqual(self._request("/api/positions").status_code, 200)

    def test_positions_sort_subsecond_timestamps_chronologically(self) -> None:
        """Keep timestamp ordering precise below whole-second resolution."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 2, 2, 0, 0),
            )
            connection.executemany(
                "INSERT INTO positions("
                "position_id, strategy, is_associated, symbol_family, symbol_raw, "
                "direction, entry_at, exit_at, "
                "status, volume_requested, volume_executed, entry_price, exit_price, commission, "
                "swap, pnl, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        "a-later", None, 0, None, "WINQ26", "buy",
                        "2026-08-01T10:00:00.000400-03:00", None, "open", None, None,
                        None, None, 0, 0, 0, 1,
                    ),
                    (
                        "z-earlier", None, 0, None, "WINQ26", "buy",
                        "2026-08-01T10:00:00.000100-03:00", None, "open", None, None,
                        None, None, 0, 0, 0, 1,
                    ),
                ],
            )

        response = self._request(
            "/api/positions?status=open&sort_by=opened_at&sort_order=asc"
        )

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
                "position_id, strategy, is_associated, symbol_family, symbol_raw, "
                "direction, entry_at, exit_at, "
                "status, volume_requested, volume_executed, entry_price, exit_price, commission, "
                "swap, pnl, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "naive", None, 0, None, "WINQ26", "buy", "2026-08-01T10:00:00", None,
                    "open", None, None, None, None, 0, 0, 0, 1,
                ),
            )

        response = self._request("/api/positions?status=open&sort_by=opened_at")

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
