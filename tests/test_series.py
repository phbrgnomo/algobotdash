"""Integration coverage for additive charts and shared multi-value filters."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import httpx

from algobotdash import storage
from algobotdash.series_limits import SeriesBudget
from algobotdash.storage import SCHEMA
from algobotdash.web import app
from tests.fixture_helpers import get_asgi, insert_positions, insert_transactions


class SeriesTests(unittest.TestCase):  # pylint: disable=too-many-public-methods
    """Verify plotted performance against independently known position outcomes."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(  # pylint: disable=consider-using-with
            prefix="algobotdash-series-"
        )
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.database = root / "projection.sqlite"
        config = root / "config.yaml"
        config.write_text(
            "timezone: America/Bahia\nsource:\n  path: fixture.xlsx\n", encoding="utf-8"
        )
        self.paths = patch.multiple(
            "algobotdash.web", CONFIG_PATH=config, DATABASE_PATH=self.database
        )
        self.paths.start()
        self.addCleanup(self.paths.stop)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT INTO imports VALUES (1,'fixture','hash','2026-01-10',8,8,0,0)"
            )
            insert_positions(connection, [
                self.row("1", "WIN", "Turtle", "2026-01-10T12:00:00Z", 10),
                self.row("2", "WIN", "FVG", "2026-01-10T14:00:00Z", -4),
                self.row("3", "WDO", "Turtle", "2026-01-12T12:00:00Z", 7),
                self.row("4", "WDO", "FVG", "2026-02-20T12:00:00Z", 3),
                self.row("5", "WDO", "Turtle", "2026-01-12T14:00:00Z", 5, associated=0),
                self.row("6", "WIN", None, "2026-01-11T12:00:00Z", 2),
                self.row("7", "WIN", "Turtle", None, 9999),
                self.row("8", "WIN", "Turtle", "2027-01-01T01:30:00Z", -20),
            ])

    @contextmanager
    def connection(self):
        """Commit fixture changes and always close the SQLite connection."""
        with closing(sqlite3.connect(self.database)) as connection:
            with connection:
                yield connection

    @staticmethod
    def row(identifier, symbol, strategy, at, pnl, *, associated=1):  # pylint: disable=too-many-arguments
        """Build a position whose non-realized amount is deliberately misleading."""
        return (identifier, strategy, symbol, f"{symbol}Q26", "buy",
                "2026-01-10T10:00:00Z", at, "closed" if at else "open",
                1, 1, 100, 110 if at else None, 0, 0, pnl, associated, 1)

    def request(self, endpoint="series", query=""):
        """Keep period limits identical across all tested resources."""
        response = get_asgi(app, f"/api/{endpoint}?date_from=2026-01-10&date_to=2026-02-20&{query}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_month_bars_and_decomposed_lines_reconcile_with_metrics_and_table(self):
        """Include residuals, exclude open P&L and clip the two calendar months."""
        payload = self.request(query="granularity=month")
        self.assertEqual(payload["state"], "available")
        self.assertEqual(payload["projection_revision"], "fixture")
        self.assertEqual([(p["start"], p["end"], p["pnl"], p["cumulative"], p["partial"])
                          for p in payload["periods"]], [
                              ("2026-01-10", "2026-01-31", 20, 20, True),
                              ("2026-02-01", "2026-02-20", 3, 23, True),
                          ])
        self.assertEqual(len(payload["strategies"]), 6)
        for index, period in enumerate(payload["periods"]):
            self.assertEqual(
                sum(s["points"][index]["pnl"] for s in payload["strategies"]), period["pnl"]
            )
            self.assertEqual(
                sum(s["points"][index]["cumulative"] for s in payload["strategies"]),
                period["cumulative"],
            )
        self.assertEqual(self.request("metrics")["net_pnl"], 23)
        self.assertEqual(self.request("positions", "limit=1")["total"], 6)

    def test_independent_multi_selects_include_all_matching_combinations(self):
        """OR within dimensions and AND between dimensions preserve four curves."""
        query = "strategy=Turtle&strategy=FVG&strategy=Turtle&symbol_family=WIN&symbol_family=WDO"
        payload = self.request(query=query)
        self.assertEqual({s["label"] for s in payload["strategies"]},
                         {"WIN Turtle", "WIN FVG", "WDO Turtle", "WDO FVG"})
        self.assertEqual(payload["periods"][-1]["cumulative"], 16)
        self.assertEqual(self.request("metrics", query)["net_pnl"], 16)
        self.assertEqual(self.request("positions", query)["total"], 4)

    def test_residual_only_and_residual_plus_named_strategy(self):
        """Unproven strategy text never becomes a classified strategy curve."""
        residual = self.request(query="include_unclassified=true")
        self.assertEqual({s["strategy"] for s in residual["strategies"]}, {None})
        self.assertEqual(residual["periods"][-1]["cumulative"], 7)
        query = "strategy=Turtle&include_unclassified=true"
        self.assertEqual(self.request("metrics", query)["net_pnl"], 24)
        self.assertEqual(self.request(query=query)["periods"][-1]["cumulative"], 24)

    def test_empty_selection_is_not_omitted_filter(self):
        """Empty selection stays empty consistently in every resource."""
        query = "selection_empty=true"
        self.assertEqual(self.request(query=query)["state"], "empty_selection")
        self.assertEqual(self.request("positions", query)["total"], 0)
        self.assertEqual(self.request("metrics", query)["sample_size"], 0)

    def test_open_and_missing_samples_have_no_fabricated_performance(self):
        """Only closed positions can produce chart observations."""
        for query, state in (("status=open", "unavailable"), ("strategy=Missing", "empty_sample")):
            payload = self.request(query=query)
            self.assertEqual(payload["state"], state)
            self.assertEqual(payload["periods"], [])
            self.assertEqual(payload["strategies"], [])

    def test_week_month_year_use_calendar_and_local_closing_date(self):
        """Weekly Monday boundaries and local New Year's Eve are independent of UTC."""
        weekly = self.request(query="granularity=week")
        self.assertEqual([(p["start"], p["end"]) for p in weekly["periods"]], [
            ("2026-01-10", "2026-01-11"), ("2026-01-12", "2026-01-18"),
            ("2026-02-16", "2026-02-20"),
        ])
        response = get_asgi(
            app, "/api/series?granularity=year&date_from=2026-01-01&date_to=2026-12-31"
        )
        self.assertEqual(response.json()["periods"], [{
            "start": "2026-01-01", "end": "2026-12-31", "partial": False,
            "pnl": 3, "cumulative": 3,
        }])

    def test_intraday_drawdown_is_not_resampled_with_performance_bars(self):
        """An intraday loss and recovery survives annual aggregation."""
        with self.connection() as connection:
            connection.execute("DELETE FROM positions")
            insert_positions(connection, [
                self.row("1", "WIN", "Turtle", "2026-01-12T12:00:00Z", -10),
                self.row("2", "WIN", "Turtle", "2026-01-12T13:00:00Z", 10),
            ])
            insert_transactions(connection, [
                ("1", None, None, None, "2026-01-12T10:00:00Z", "", "balance",
                 None, None, 0, 0, 0, 1000, 100, "Ajuste de Saldo", 1),
            ])
        payload = self.request(query="granularity=year")
        self.assertEqual(payload["periods"][0]["pnl"], 0)
        self.assertEqual(
            [p["depth"] for p in payload["drawdown"]["monetary"]["points"]], [0, -10, 0]
        )
        self.assertEqual([p["depth"] for p in payload["drawdown"]["percentage"]["points"]], [0, 0])
        metrics = self.request("metrics")
        self.assertEqual(metrics["monetary_drawdown"]["deepest_episode"]["depth"], -10)
        self.assertEqual(metrics["percentage_drawdown"]["state"], "no_drawdown")

    def test_percentage_unavailability_does_not_hide_monetary_series(self):
        """Missing opening capital affects only the percentage view."""
        payload = self.request()
        self.assertGreater(len(payload["drawdown"]["monetary"]["points"]), 0)
        self.assertEqual(payload["drawdown"]["percentage"]["state"], "unavailable")
        self.assertEqual(
            payload["drawdown"]["percentage"]["reason"], "invalid_opening_balance_coverage"
        )

    def test_new_highs_have_zero_depth_and_percentage_loss_is_not_capped(self):
        """A fresh peak is not positive drawdown; losses beyond capital stay visible."""
        with self.connection() as connection:
            connection.execute("DELETE FROM positions")
            insert_positions(connection, [
                self.row("1", "WIN", "Turtle", "2026-01-12T12:00:00Z", 10),
                self.row("2", "WIN", "Turtle", "2026-01-13T12:00:00Z", -150),
            ])
            insert_transactions(connection, [
                (str(day), None, None, None, f"2026-01-{day}T10:00:00Z", "", "balance",
                 None, None, 0, 0, 0, 1000, 100, "Ajuste de Saldo", 1)
                for day in (12, 13)
            ])
        payload = self.request()
        self.assertEqual(
            [p["depth"] for p in payload["drawdown"]["monetary"]["points"]], [0, 0, -150]
        )
        self.assertEqual(
            [p["depth"] for p in payload["drawdown"]["percentage"]["points"]], [0, 0, -1.5]
        )

    def test_decimal_recovery_and_series_overflow_do_not_publish_partial_values(self):
        """Keep decimal recovery exact and reject an unrepresentable trajectory."""
        with self.connection() as connection:
            connection.execute("DELETE FROM positions")
            insert_positions(connection, [
                self.row(str(day), "WIN", "Turtle", f"2026-01-{day}T12:00:00Z", pnl)
                for day, pnl in ((12, .1), (13, .2), (14, -.3))
            ])
        self.assertEqual(self.request()["periods"][-1]["cumulative"], 0)
        self.assertEqual(self.request("metrics")["net_pnl"], 0)
        self.assertEqual(self.request("metrics")["expectancy"], 0)
        self.assertEqual(self.request(query="granularity=month")["periods"][-1]["cumulative"], 0)
        with self.connection() as connection:
            connection.execute("UPDATE positions SET pnl=1e308")
        payload = self.request()
        self.assertEqual(payload["state"], "unavailable")
        self.assertEqual(payload["reason"], "numeric_overflow")
        self.assertEqual(payload["periods"], [])
        self.assertEqual(payload["strategies"], [])

    def test_invalid_filters_and_granularity_remain_controlled(self):
        """New dimensions share the existing validation contract."""
        for query in ("granularity=quarter", "strategy=%20", "symbol_family=",
                      "date_from=2026-02-02&date_to=2026-01-01"):
            self.assertEqual(get_asgi(app, f"/api/series?{query}").status_code, 422)

    def test_raw_symbol_remains_selectable_when_family_is_missing(self):
        """Unknown normalized families must not disappear from the additive total."""
        with self.connection() as connection:
            connection.execute("UPDATE positions SET symbol_family=NULL WHERE position_id='1'")
        options = get_asgi(app, "/api/filter-options").json()
        self.assertIn("WINQ26", options["symbol_families"])
        payload = self.request(query="symbol_family=WINQ26")
        self.assertEqual(payload["periods"][-1]["cumulative"], 10)
        self.assertEqual(payload["strategies"][0]["symbol_family"], "WINQ26")
        self.assertEqual(
            self.request("positions", "symbol_family=WINQ26")["items"][0]["symbol_family"],
            "WINQ26",
        )

    def test_large_combined_filters_work_under_sqlite_999_variable_limit(self):
        """Bind each dimension as one JSON value, preserving full accepted selections."""
        if not hasattr(sqlite3.Connection, "setlimit"):
            self.skipTest("requires Python 3.11+ for SQLite runtime limits")
        open_projection = storage._open_projection  # pylint: disable=protected-access

        def limited_connection(*args, **kwargs):
            connection = open_projection(*args, **kwargs)
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            return connection

        query = urlencode(
            [("strategy", value) for value in ["Turtle", "FVG"] +
             [f"Unused{i}" for i in range(498)]] +
            [("symbol_family", value) for value in ["WIN", "WDO"] +
             [f"Unknown{i}" for i in range(498)]]
        )
        with patch("algobotdash.storage._open_projection", side_effect=limited_connection):
            self.assertEqual(self.request("positions", query)["total"], 4)
            self.assertEqual(self.request("metrics", query)["net_pnl"], 16)
            self.assertEqual(self.request(query=query)["periods"][-1]["cumulative"], 16)

    def test_readers_reject_a_different_published_revision(self):
        """Every sample endpoint must honor the same requested immutable revision."""
        for endpoint in ("positions", "metrics", "series"):
            self.request(endpoint, "projection_revision=fixture")
        with self.connection() as connection:
            connection.execute("UPDATE projection_metadata SET revision='next'")
        for endpoint in ("positions", "metrics", "series"):
            response = get_asgi(app, f"/api/{endpoint}?projection_revision=fixture")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "projection_revision_changed")
            self.request(endpoint, "projection_revision=next")

    def test_series_limits_reject_rows_ledger_and_dense_points(self):
        """Reject before materializing a large read or a dense period/strategy matrix."""
        for setting, limit in (("algobotdash.web.MAX_SERIES_ROWS", 2),
                               ("algobotdash.series.MAX_SERIES_POINTS", 2)):
            with patch(setting, limit):
                response = get_asgi(app, "/api/series")
            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.json()["detail"]["code"], "series_too_large")
        with self.connection() as connection:
            insert_transactions(connection, [
                (str(index), None, None, None, "2026-01-12T10:00:00Z", "", "balance",
                 None, None, 0, 0, 0, 100, 100, "Ajuste de Saldo", 1)
                for index in range(11)
            ])
        with patch("algobotdash.web.MAX_SERIES_ROWS", 10):
            self.assertEqual(get_asgi(app, "/api/series").status_code, 413)

    def test_series_rate_limit_is_explicit_and_recovers(self):
        """Rate rejections carry Retry-After and expire without queuing work."""
        budget = SeriesBudget()
        with patch("algobotdash.web.series_budget", return_value=budget):
            for _ in range(30):
                self.assertEqual(get_asgi(app, "/api/series").status_code, 200)
            response = get_asgi(app, "/api/series")
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        self.assertEqual(response.json()["detail"]["code"], "series_rate_limit")
        with patch("algobotdash.series_limits.time.monotonic", return_value=float("inf")):
            budget.start()
        budget.finish()

    def test_busy_series_workers_do_not_block_reads_or_release_on_client_cancellation(self):
        """Health-like reads stay responsive and cancelling HTTP cannot bypass admission."""
        release = threading.Event()
        entered = threading.Barrier(3)
        budget = SeriesBudget()
        original_read = storage.read_metric_sample

        def blocked_read(*args, **kwargs):
            entered.wait(timeout=3)
            if not release.wait(timeout=3):
                raise AssertionError("series worker was not released")
            return original_read(*args, **kwargs)

        async def requests():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                first = asyncio.create_task(client.get("/api/series"))
                second = asyncio.create_task(client.get("/api/series"))
                try:
                    await asyncio.to_thread(entered.wait, 3)
                    response = await asyncio.wait_for(client.get("/api/strategies"), timeout=1)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual((await client.get("/api/series")).status_code, 429)
                    first.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
                    self.assertEqual((await client.get("/api/series")).status_code, 429)
                finally:
                    release.set()
                    await asyncio.gather(first, second, return_exceptions=True)

        with patch("algobotdash.web.read_metric_sample", side_effect=blocked_read), \
             patch("algobotdash.web.series_budget", return_value=budget):
            asyncio.run(requests())
        # asyncio.run waits for admitted shielded workers before closing its executor.
        budget.start()
        budget.finish()
