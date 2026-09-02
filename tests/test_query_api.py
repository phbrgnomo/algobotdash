"""Integration tests for the read-only dashboard query API."""

from __future__ import annotations

import math
import shutil
import sqlite3
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from algobotdash.metrics import _finite_ratio  # pylint: disable=protected-access
from algobotdash.storage import SCHEMA, read_positions
from algobotdash.web import app
from tests.fixture_helpers import get_asgi, insert_positions


# The integration seam intentionally covers every public read-only API behavior.
# pylint: disable=too-many-public-methods
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

    def test_metrics_calculate_realized_position_statistics(self) -> None:
        """Expose the agreed realized metrics for one controlled P&L sample."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 4, 4, 0, 0),
            )
            insert_positions(
                connection,
                [
                    (
                        str(position_id), "Turtle", "WIN", "WINQ26", "buy",
                        f"2026-08-0{position_id}T10:00:00-03:00",
                        f"2026-08-0{position_id}T11:00:00-03:00",
                        "closed", 1, 1, 100, 101, 0, 0, pnl, 1, 1,
                    )
                    for position_id, pnl in enumerate((100, -40, 0, 20), start=1)
                ],
            )

        response = self._request("/api/metrics")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sample_size"], 4)
        self.assertEqual(payload["excluded_open_positions"], 0)
        self.assertEqual(payload["net_pnl"], 80)
        self.assertEqual(payload["gross_profit"], 120)
        self.assertEqual(payload["gross_loss"], -40)
        self.assertEqual((payload["winning_trades"], payload["losing_trades"]), (2, 1))
        self.assertEqual(payload["win_rate"], 0.5)
        self.assertEqual(payload["profit_factor"], 3)
        self.assertEqual(payload["payoff"], 1.5)
        self.assertEqual(payload["expectancy"], 20)
        self.assertAlmostEqual(payload["sharpe_per_position"], 0.3396831102433787)
        self.assertEqual(payload["sortino_per_position"], 1)
        self.assertEqual(payload["unavailable_reasons"], {})

    def test_metrics_keep_empty_realized_sums_available(self) -> None:
        """Distinguish an empty realized sample from unavailable metrics."""
        self._seed_projection()

        response = self._request("/api/metrics?strategy=Missing")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sample_size"], 0)
        self.assertEqual(payload["excluded_open_positions"], 0)
        self.assertEqual(payload["net_pnl"], 0)
        self.assertEqual(payload["gross_profit"], 0)
        self.assertEqual(payload["gross_loss"], 0)
        self.assertEqual((payload["winning_trades"], payload["losing_trades"]), (0, 0))
        for metric in (
            "win_rate", "profit_factor", "payoff", "expectancy",
            "sharpe_per_position", "sortino_per_position",
        ):
            with self.subTest(metric=metric):
                self.assertIsNone(payload[metric])
                self.assertEqual(payload["unavailable_reasons"][metric], "empty_sample")

    def test_metrics_explain_absent_losses_and_downside(self) -> None:
        """Return finite values and stable reasons when no outcome is negative."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 2, 2, 0, 0),
            )
            insert_positions(
                connection,
                [
                    (
                        "1", "Turtle", "WIN", "WINQ26", "buy",
                        "2026-08-01T10:00:00-03:00", "2026-08-01T11:00:00-03:00",
                        "closed", 1, 1, 100, 101, 0, 0, 10, 1, 1,
                    ),
                    (
                        "2", "Turtle", "WIN", "WINQ26", "buy",
                        "2026-08-02T10:00:00-03:00", "2026-08-02T11:00:00-03:00",
                        "closed", 1, 1, 100, 101, 0, 0, 20, 1, 1,
                    ),
                ],
            )

        payload = self._request("/api/metrics").json()

        self.assertIsNone(payload["profit_factor"])
        self.assertEqual(payload["unavailable_reasons"]["profit_factor"], "no_losing_positions")
        self.assertIsNone(payload["payoff"])
        self.assertEqual(payload["unavailable_reasons"]["payoff"], "no_losing_positions")
        self.assertIsNone(payload["sortino_per_position"])
        self.assertEqual(
            payload["unavailable_reasons"]["sortino_per_position"],
            "zero_downside_deviation",
        )
        self.assertNotIn("sharpe_per_position", payload["unavailable_reasons"])

    def test_metrics_explain_absent_wins(self) -> None:
        """Keep profit factor finite while payoff has no winning denominator."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 2, 2, 0, 0),
            )
            insert_positions(
                connection,
                [
                    (
                        str(position_id), "Turtle", "WIN", "WINQ26", "buy",
                        f"2026-08-0{position_id}T10:00:00-03:00",
                        f"2026-08-0{position_id}T11:00:00-03:00",
                        "closed", 1, 1, 100, 99, 0, 0, pnl, 1, 1,
                    )
                    for position_id, pnl in enumerate((-10, -20), start=1)
                ],
            )

        payload = self._request("/api/metrics").json()

        self.assertEqual(payload["profit_factor"], 0)
        self.assertIsNone(payload["payoff"])
        self.assertEqual(payload["unavailable_reasons"]["payoff"], "no_winning_positions")
        self.assertNotIn("profit_factor", payload["unavailable_reasons"])

    def test_metrics_require_two_positions_for_distribution_ratios(self) -> None:
        """Do not present Sharpe or Sortino for a single realized position."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 1, 1, 0, 0),
            )
            insert_positions(
                connection,
                [(
                    "1", "Turtle", "WIN", "WINQ26", "buy",
                    "2026-08-01T10:00:00-03:00", "2026-08-01T11:00:00-03:00",
                    "closed", 1, 1, 100, 99, 0, 0, -10, 1, 1,
                )],
            )

        payload = self._request("/api/metrics").json()

        self.assertIsNone(payload["sharpe_per_position"])
        self.assertIsNone(payload["sortino_per_position"])
        self.assertEqual(
            payload["unavailable_reasons"]["sharpe_per_position"],
            "insufficient_sample",
        )
        self.assertEqual(
            payload["unavailable_reasons"]["sortino_per_position"],
            "insufficient_sample",
        )

    def test_metrics_explain_zero_standard_deviation(self) -> None:
        """Return a reason instead of infinity for identical outcomes."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 2, 2, 0, 0),
            )
            insert_positions(
                connection,
                [
                    (
                        str(position_id), "Turtle", "WIN", "WINQ26", "buy",
                        f"2026-08-0{position_id}T10:00:00-03:00",
                        f"2026-08-0{position_id}T11:00:00-03:00",
                        "closed", 1, 1, 100, 101, 0, 0, 5, 1, 1,
                    )
                    for position_id in (1, 2)
                ],
            )

        payload = self._request("/api/metrics").json()

        self.assertIsNone(payload["sharpe_per_position"])
        self.assertEqual(
            payload["unavailable_reasons"]["sharpe_per_position"],
            "zero_standard_deviation",
        )

    def test_metrics_distinguish_all_and_open_status(self) -> None:
        """Count excluded opens for all and make open metrics unavailable."""
        self._seed_projection()

        all_positions = self._request("/api/metrics?status=all")
        open_positions = self._request("/api/metrics?status=open")

        self.assertEqual(all_positions.status_code, 200)
        self.assertEqual(all_positions.json()["sample_size"], 2)
        self.assertEqual(all_positions.json()["excluded_open_positions"], 1)
        self.assertEqual(all_positions.json()["net_pnl"], -12)
        self.assertEqual(open_positions.status_code, 200)
        self.assertEqual(open_positions.json()["sample_size"], 0)
        self.assertEqual(open_positions.json()["excluded_open_positions"], 1)
        for metric in (
            "net_pnl", "gross_profit", "gross_loss", "winning_trades", "losing_trades",
            "win_rate", "profit_factor", "payoff", "expectancy",
            "sharpe_per_position", "sortino_per_position",
        ):
            with self.subTest(metric=metric):
                self.assertIsNone(open_positions.json()[metric])
                self.assertEqual(
                    open_positions.json()["unavailable_reasons"][metric],
                    "realized_metrics_unavailable_for_open_status",
                )

    def test_metrics_apply_shared_filters_without_assigning_unassociated_strategy(self) -> None:
        """Include unassociated totals but require association for strategy metrics."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 4, 4, 1, 0),
            )
            insert_positions(
                connection,
                [
                    (
                        "1", "Turtle", "WIN", "WINQ26", "buy",
                        "2026-08-01T10:00:00-03:00", "2026-08-01T11:00:00-03:00",
                        "closed", 1, 1, 100, 101, 0, 0, 10, 1, 1,
                    ),
                    (
                        "2", "Turtle", "WDO", "WDOQ26", "sell",
                        "2026-08-02T10:00:00-03:00", "2026-08-02T11:00:00-03:00",
                        "closed", 1, 1, 100, 101, 0, 0, 20, 0, 1,
                    ),
                    (
                        "3", "FVG", "WIN", "WINQ26", "sell",
                        "2026-08-03T10:00:00-03:00", "2026-08-03T11:00:00-03:00",
                        "closed", 1, 1, 100, 99, 0, 0, -5, 1, 1,
                    ),
                    (
                        "4", None, "WDO", "WDOQ26", "sell",
                        "2026-08-02T12:00:00-03:00", None,
                        "open", 1, 1, 100, None, 0, 0, 999, 0, 1,
                    ),
                ],
            )

        general = self._request("/api/metrics")
        strategy = self._request("/api/metrics?strategy=Turtle")
        unassociated = self._request(
            "/api/metrics?status=all&association=unassociated&symbol_family=WDO"
            "&direction=sell&date_from=2026-08-02&date_to=2026-08-02"
        )

        self.assertEqual(general.json()["sample_size"], 3)
        self.assertEqual(general.json()["net_pnl"], 25)
        self.assertEqual(strategy.json()["sample_size"], 1)
        self.assertEqual(strategy.json()["net_pnl"], 10)
        self.assertEqual(unassociated.json()["sample_size"], 1)
        self.assertEqual(unassociated.json()["net_pnl"], 20)
        self.assertEqual(unassociated.json()["excluded_open_positions"], 1)

    def test_each_metrics_filter_is_independently_effective(self) -> None:
        """Make every shared metric predicate observable against control rows."""
        self._seed_projection()

        strategy = self._request("/api/metrics?status=all&strategy=Turtle").json()
        symbol = self._request("/api/metrics?status=all&symbol_family=WDO").json()
        direction = self._request("/api/metrics?status=all&direction=sell").json()
        associated = self._request(
            "/api/metrics?status=all&association=associated"
        ).json()
        unassociated = self._request(
            "/api/metrics?status=all&association=unassociated"
        ).json()
        period = self._request(
            "/api/metrics?status=all&date_from=2026-08-03&date_to=2026-08-03"
        ).json()
        upper_bound = self._request(
            "/api/metrics?status=all&date_to=2026-08-01"
        ).json()

        self.assertEqual((strategy["sample_size"], strategy["net_pnl"]), (1, 9))
        self.assertEqual(
            (symbol["sample_size"], symbol["excluded_open_positions"]), (0, 1)
        )
        self.assertEqual(
            (direction["sample_size"], direction["excluded_open_positions"]),
            (0, 1),
        )
        self.assertEqual(
            (associated["sample_size"], associated["excluded_open_positions"]),
            (2, 0),
        )
        self.assertEqual(
            (unassociated["sample_size"], unassociated["excluded_open_positions"]),
            (0, 1),
        )
        self.assertEqual(
            (period["sample_size"], period["net_pnl"], period["excluded_open_positions"]),
            (1, -21, 0),
        )
        self.assertEqual(
            (
                upper_bound["sample_size"],
                upper_bound["net_pnl"],
                upper_bound["excluded_open_positions"],
            ),
            (1, 9, 0),
        )

    def test_metrics_reject_additive_numeric_overflow(self) -> None:
        """Translate an unrepresentable monetary aggregate into the stable 503."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 2, 2, 0, 0),
            )
            insert_positions(
                connection,
                [
                    (
                        str(position_id), "Turtle", "WIN", "WINQ26", "buy",
                        f"2026-08-0{position_id}T10:00:00-03:00",
                        f"2026-08-0{position_id}T11:00:00-03:00",
                        "closed", 1, 1, 100, 101, 0, 0, 1e308, 1, 1,
                    )
                    for position_id in (1, 2)
                ],
            )

        response = self._request("/api/metrics")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"detail": {"code": "projection_unavailable"}}
        )

    def test_metrics_keep_extreme_downside_calculation_finite(self) -> None:
        """Avoid overflow while calculating downside deviation."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 2, 2, 0, 0),
            )
            insert_positions(
                connection,
                [
                    (
                        str(position_id), "Turtle", "WIN", "WINQ26", "buy",
                        f"2026-08-0{position_id}T10:00:00-03:00",
                        f"2026-08-0{position_id}T11:00:00-03:00",
                        "closed", 1, 1, 100, 101, 0, 0, pnl, 1, 1,
                    )
                    for position_id, pnl in enumerate((1e308, -1e308), start=1)
                ],
            )

        response = self._request("/api/metrics")

        self.assertEqual(response.status_code, 200)
        for metric in (
            "net_pnl", "gross_profit", "gross_loss", "win_rate", "profit_factor",
            "payoff", "expectancy", "sharpe_per_position", "sortino_per_position",
        ):
            with self.subTest(metric=metric):
                self.assertTrue(math.isfinite(response.json()[metric]))

    def test_metrics_explain_nonrepresentable_ratios(self) -> None:
        """Return null with an explicit reason instead of infinite ratios."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 2, 2, 0, 0),
            )
            insert_positions(
                connection,
                [
                    (
                        str(position_id), "Turtle", "WIN", "WINQ26", "buy",
                        f"2026-08-0{position_id}T10:00:00-03:00",
                        f"2026-08-0{position_id}T11:00:00-03:00",
                        "closed", 1, 1, 100, 101, 0, 0, pnl, 1, 1,
                    )
                    for position_id, pnl in enumerate((1e308, -5e-324), start=1)
                ],
            )

        response = self._request("/api/metrics")

        self.assertEqual(response.status_code, 200)
        for metric in ("profit_factor", "payoff", "sortino_per_position"):
            with self.subTest(metric=metric):
                self.assertIsNone(response.json()[metric])
                self.assertEqual(
                    response.json()["unavailable_reasons"][metric],
                    "numeric_overflow",
                )
        self.assertIsNone(_finite_ratio(1.0, 0.0))
        self.assertIsNone(_finite_ratio(1.0, -0.0))

    def test_metrics_isolate_standard_deviation_overflow(self) -> None:
        """Keep representable metrics when only sample deviation overflows."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 2, 2, 0, 0),
            )
            insert_positions(
                connection,
                [
                    (
                        str(position_id), "Turtle", "WIN", "WINQ26", "buy",
                        f"2026-08-0{position_id}T10:00:00-03:00",
                        f"2026-08-0{position_id}T11:00:00-03:00",
                        "closed", 1, 1, 100, 101, 0, 0, pnl, 1, 1,
                    )
                    for position_id, pnl in enumerate(
                        (sys.float_info.max, -sys.float_info.max), start=1
                    )
                ],
            )

        response = self._request("/api/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["sharpe_per_position"])
        self.assertEqual(
            response.json()["unavailable_reasons"]["sharpe_per_position"],
            "numeric_overflow",
        )
        for metric in (
            "net_pnl", "gross_profit", "gross_loss", "win_rate", "profit_factor",
            "payoff", "expectancy", "sortino_per_position",
        ):
            with self.subTest(metric=metric):
                self.assertTrue(math.isfinite(response.json()[metric]))

    def test_metrics_validate_the_shared_filter_contract(self) -> None:
        """Use the same validation and empty-sample behavior as positions."""
        self._seed_projection()

        inverted = self._request(
            "/api/metrics?date_from=2026-08-03&date_to=2026-08-01"
        )
        contradictory = self._request(
            "/api/metrics?strategy=Turtle&association=unassociated"
        )
        blank = self._request("/api/metrics?symbol_family=%20%20")
        unknown = self._request("/api/metrics?strategy=Missing")

        self.assertEqual(inverted.status_code, 422)
        self.assertEqual(inverted.json()["detail"]["code"], "invalid_date_range")
        self.assertEqual(contradictory.status_code, 422)
        self.assertEqual(
            contradictory.json()["detail"]["code"], "contradictory_filters"
        )
        self.assertEqual(blank.status_code, 422)
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(unknown.json()["sample_size"], 0)
        self.assertEqual(
            self._request("/api/metrics?direction=sideways").status_code, 422
        )
        self.assertEqual(
            self._request("/api/metrics?status=finished").status_code, 422
        )
        self.assertEqual(
            self._request("/api/metrics?association=maybe").status_code, 422
        )

    def test_metrics_reject_malformed_projection(self) -> None:
        """Expose the stable projection error instead of partial metrics."""
        self.database_path.write_bytes(b"not a sqlite database")

        response = self._request("/api/metrics")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"detail": {"code": "projection_unavailable"}}
        )

    def test_metrics_reject_invalid_pnl_in_projection(self) -> None:
        """Translate a malformed persisted P&L into the stable projection error."""
        with self._projection() as connection:
            connection.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "ReportHistory.xlsx", "hash", "2026-08-01T00:00:00+00:00", 1, 1, 0, 0),
            )
            insert_positions(
                connection,
                [(
                    "1", "Turtle", "WIN", "WINQ26", "buy",
                    "2026-08-01T10:00:00-03:00", "2026-08-01T11:00:00-03:00",
                    "closed", 1, 1, 100, 101, 0, 0, "not-a-number", 1, 1,
                )],
            )

        response = self._request("/api/metrics")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"detail": {"code": "projection_unavailable"}}
        )

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
                self.assertEqual(response.json()["total"], len(expected))

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

    def test_filter_options_reject_malformed_projection(self) -> None:
        """Return the public projection error when SQLite cannot be queried."""
        self.database_path.write_bytes(b"not a sqlite database")

        response = self._request("/api/filter-options")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": {"code": "projection_unavailable"}},
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
