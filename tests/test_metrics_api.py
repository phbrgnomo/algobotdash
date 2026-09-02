# pylint: disable=duplicate-code
"""Integration tests for the realized metrics API endpoint."""

from __future__ import annotations

import math
import shutil
import sqlite3
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from algobotdash.storage import SCHEMA
from algobotdash.web import app
from tests.fixture_helpers import get_asgi, insert_positions


class MetricsApiTests(unittest.TestCase):
    """Verify realized metric calculation, filter combinations, and ratio failure reasons."""

    tmp_path: Path = Path()
    config_path: Path = Path()
    database_path: Path = Path()

    def setUp(self) -> None:
        self.tmp_path = Path(tempfile.mkdtemp(prefix="algobotdash-metrics-tests-"))
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

    def _seed_import(self, connection: sqlite3.Connection, import_id: int = 1) -> None:
        """Insert a parent import row to satisfy foreign key constraints."""
        connection.execute(
            "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                import_id,
                f"Report{import_id}.xlsx",
                f"hash{import_id}",
                "2026-08-31T10:00:00+00:00",
                5,
                5,
                0,
                0,
            ),
        )

    def _seed_sample_projection(self) -> None:
        """Seed a representative set of closed and open analytical positions."""
        with self._projection() as connection:
            self._seed_import(connection, 1)
            insert_positions(
                connection,
                [
                    # 1: Win (+100) Turtle closed
                    (
                        "1", "Turtle", "WIN", "WINQ26", "buy", "2026-08-01T10:00:00-03:00",
                        "2026-08-01T11:00:00-03:00", "closed", 1, 1, 100, 110, -1, 0, 100.0, 1, 1,
                    ),
                    # 2: Loss (-50) Turtle closed
                    (
                        "2", "Turtle", "WIN", "WINQ26", "sell", "2026-08-02T10:00:00-03:00",
                        "2026-08-02T11:00:00-03:00", "closed", 1, 1, 100, 90, -1, 0, -50.0, 1, 1,
                    ),
                    # 3: Tie (0) FVG closed
                    (
                        "3", "FVG", "WDO", "WDOU26", "buy", "2026-08-03T10:00:00-03:00",
                        "2026-08-03T11:00:00-03:00", "closed", 1, 1, 100, 100, 0, 0, 0.0, 1, 1,
                    ),
                    # 4: Win (+200) Unassociated closed
                    (
                        "4", None, "WIN", "WINV26", "buy", "2026-08-04T10:00:00-03:00",
                        "2026-08-04T11:00:00-03:00", "closed", 1, 1, 100, 120, -1, 0, 200.0, 0, 1,
                    ),
                    # 5: Open position (excluded from metrics)
                    (
                        "5", "Turtle", "WIN", "WINV26", "buy", "2026-08-05T10:00:00-03:00",
                        None, "open", 1, 1, 100, None, 0, 0, 0.0, 1, 1,
                    ),
                ],
            )

    def test_metrics_endpoint_calculates_realized_metrics_for_closed_positions(self) -> None:
        """Endpoint should return exact realized metrics across closed positions."""
        self._seed_sample_projection()

        response = self._request("/api/metrics?status=closed")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total_sample"], 4)
        self.assertEqual(data["excluded_open_count"], 0)
        # Closed pnls: [100.0, -50.0, 0.0, 200.0]
        # Net pnl: 250.0
        self.assertAlmostEqual(data["net_pnl"], 250.0)
        self.assertAlmostEqual(data["gross_profit"], 300.0)
        self.assertAlmostEqual(data["gross_loss"], 50.0)
        # Win rate: 2 wins / 4 total = 0.5
        self.assertAlmostEqual(data["win_rate"], 0.5)
        # Profit factor: 300 / 50 = 6.0
        self.assertAlmostEqual(data["profit_factor"], 6.0)
        # Payoff: avg win (150) / avg loss (50) = 3.0
        self.assertAlmostEqual(data["payoff"], 3.0)
        # Expectancy: 250 / 4 = 62.5
        self.assertAlmostEqual(data["expectancy"], 62.5)

        # Positions Sharpe: mean / sample std dev (N-1)
        mean = 62.5
        variance = sum((x - mean) ** 2 for x in [100.0, -50.0, 0.0, 200.0]) / 3
        std_dev = math.sqrt(variance)
        expected_sharpe = mean / std_dev
        self.assertAlmostEqual(data["position_sharpe"], expected_sharpe, places=5)

        # Position Sortino: mean / downside dev (N)
        downside_variance = sum(min(0.0, x) ** 2 for x in [100.0, -50.0, 0.0, 200.0]) / 4
        downside_dev = math.sqrt(downside_variance)
        expected_sortino = mean / downside_dev
        self.assertAlmostEqual(data["position_sortino"], expected_sortino, places=5)
        self.assertEqual(data["unavailable_reasons"], {})

    def test_metrics_endpoint_with_status_all_counts_excluded_open(self) -> None:
        """Status=all calculates metrics over closed positions and reports excluded open count."""
        self._seed_sample_projection()

        response = self._request("/api/metrics?status=all")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total_sample"], 4)
        self.assertEqual(data["excluded_open_count"], 1)
        self.assertAlmostEqual(data["net_pnl"], 250.0)

    def test_metrics_endpoint_with_status_open_returns_null_and_reasons(self) -> None:
        """Status=open excludes all open positions and marks metrics unavailable."""
        self._seed_sample_projection()

        response = self._request("/api/metrics?status=open")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total_sample"], 0)
        self.assertEqual(data["excluded_open_count"], 1)
        self.assertIsNone(data["net_pnl"])
        self.assertIsNone(data["profit_factor"])
        self.assertIsNone(data["position_sharpe"])
        self.assertEqual(
            data["unavailable_reasons"]["net_pnl"], "open_positions_only"
        )
        self.assertEqual(
            data["unavailable_reasons"]["position_sharpe"], "open_positions_only"
        )

    def test_metrics_empty_projection_returns_empty_sample_reason(self) -> None:
        """An empty projection returns null metrics with empty_sample reasons."""
        with self._projection():
            pass

        response = self._request("/api/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total_sample"], 0)
        self.assertEqual(data["excluded_open_count"], 0)
        self.assertIsNone(data["net_pnl"])
        self.assertIsNone(data["profit_factor"])
        self.assertEqual(data["unavailable_reasons"]["profit_factor"], "empty_sample")
        self.assertEqual(data["unavailable_reasons"]["position_sharpe"], "empty_sample")

    def test_metrics_filters_by_strategy_and_association(self) -> None:
        """Filtering by strategy only includes associated matching positions."""
        self._seed_sample_projection()

        response = self._request("/api/metrics?strategy=Turtle")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Turtle closed: 100.0, -50.0 (sample size = 2)
        self.assertEqual(data["total_sample"], 2)
        self.assertAlmostEqual(data["net_pnl"], 50.0)
        self.assertAlmostEqual(data["gross_profit"], 100.0)
        self.assertAlmostEqual(data["gross_loss"], 50.0)
        self.assertAlmostEqual(data["profit_factor"], 2.0)
        self.assertAlmostEqual(data["payoff"], 2.0)
        self.assertAlmostEqual(data["expectancy"], 25.0)

    def test_metrics_zero_gross_loss_reports_unavailable_reason(self) -> None:
        """When all closed positions are profitable, profit factor returns zero_gross_loss."""
        with self._projection() as connection:
            self._seed_import(connection, 1)
            insert_positions(
                connection,
                [
                    (
                        "1", "Turtle", "WIN", "WINQ26", "buy", "2026-08-01T10:00:00Z",
                        "2026-08-01T11:00:00Z", "closed", 1, 1, 100, 110, 0, 0, 100.0, 1, 1,
                    ),
                    (
                        "2", "Turtle", "WIN", "WINQ26", "buy", "2026-08-02T10:00:00Z",
                        "2026-08-02T11:00:00Z", "closed", 1, 1, 100, 120, 0, 0, 200.0, 1, 1,
                    ),
                ],
            )

        response = self._request("/api/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total_sample"], 2)
        self.assertAlmostEqual(data["gross_profit"], 300.0)
        self.assertAlmostEqual(data["gross_loss"], 0.0)
        self.assertIsNone(data["profit_factor"])
        self.assertEqual(data["unavailable_reasons"]["profit_factor"], "zero_gross_loss")
        self.assertIsNone(data["payoff"])
        self.assertEqual(data["unavailable_reasons"]["payoff"], "no_losses")
        self.assertIsNone(data["position_sortino"])
        self.assertEqual(
            data["unavailable_reasons"]["position_sortino"], "zero_downside_deviation"
        )

    def test_metrics_single_observation_reports_sample_too_small_for_sharpe(self) -> None:
        """Sample size < 2 reports sample_too_small for position Sharpe."""
        with self._projection() as connection:
            self._seed_import(connection, 1)
            insert_positions(
                connection,
                [
                    (
                        "1", "Turtle", "WIN", "WINQ26", "buy", "2026-08-01T10:00:00Z",
                        "2026-08-01T11:00:00Z", "closed", 1, 1, 100, 110, 0, 0, 50.0, 1, 1,
                    ),
                ],
            )

        response = self._request("/api/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total_sample"], 1)
        self.assertIsNone(data["position_sharpe"])
        self.assertEqual(
            data["unavailable_reasons"]["position_sharpe"], "sample_too_small"
        )

    def test_metrics_zero_dispersion_reports_reason(self) -> None:
        """Sample with identical values reports zero_dispersion for position Sharpe."""
        with self._projection() as connection:
            self._seed_import(connection, 1)
            insert_positions(
                connection,
                [
                    (
                        "1", "Turtle", "WIN", "WINQ26", "buy", "2026-08-01T10:00:00Z",
                        "2026-08-01T11:00:00Z", "closed", 1, 1, 100, 110, 0, 0, 50.0, 1, 1,
                    ),
                    (
                        "2", "Turtle", "WIN", "WINQ26", "buy", "2026-08-02T10:00:00Z",
                        "2026-08-02T11:00:00Z", "closed", 1, 1, 100, 110, 0, 0, 50.0, 1, 1,
                    ),
                ],
            )

        response = self._request("/api/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total_sample"], 2)
        self.assertIsNone(data["position_sharpe"])
        self.assertEqual(
            data["unavailable_reasons"]["position_sharpe"], "zero_dispersion"
        )

    def test_metrics_validations_reject_invalid_date_range_and_contradictions(self) -> None:
        """API rejects date_from > date_to and strategy combined with unassociated."""
        self._seed_sample_projection()

        res1 = self._request("/api/metrics?date_from=2026-08-10&date_to=2026-08-01")
        self.assertEqual(res1.status_code, 422)
        self.assertEqual(res1.json()["detail"]["code"], "invalid_date_range")

        res2 = self._request("/api/metrics?strategy=Turtle&association=unassociated")
        self.assertEqual(res2.status_code, 422)
        self.assertEqual(res2.json()["detail"]["code"], "contradictory_filters")

        res3 = self._request("/api/metrics?strategy=%20%20")
        self.assertEqual(res3.status_code, 422)
        self.assertEqual(res3.json()["detail"]["code"], "invalid_filter")

    def test_metrics_drawdown_recovered_and_distinct_depth_duration_episodes(self) -> None:
        """Endpoint distinguishes max depth episode from max duration episode."""
        with self._projection() as connection:
            self._seed_import(connection, 1)
            insert_positions(
                connection,
                [
                    # Day 1: +100 (Peak = 100 at 2026-08-01T10:00:00Z)
                    (
                        "1", "Turtle", "WIN", "WINQ26", "buy", "2026-08-01T09:00:00Z",
                        "2026-08-01T10:00:00Z", "closed", 1, 1, 100, 110, 0, 0, 100.0, 1, 1,
                    ),
                    # Day 2: -200 (Trough = -100, Depth = 200 at 2026-08-02T10:00:00Z)
                    (
                        "2", "Turtle", "WIN", "WINQ26", "buy", "2026-08-02T09:00:00Z",
                        "2026-08-02T10:00:00Z", "closed", 1, 1, 100, 90, 0, 0, -200.0, 1, 1,
                    ),
                    # Day 3: +250 (Cum = 150 -> Recovered at 2026-08-03T10:00:00Z, dur = 2 days)
                    # New Peak = 150 at 2026-08-03T10:00:00Z
                    (
                        "3", "Turtle", "WIN", "WINQ26", "buy", "2026-08-03T09:00:00Z",
                        "2026-08-03T10:00:00Z", "closed", 1, 1, 100, 125, 0, 0, 250.0, 1, 1,
                    ),
                    # Day 5: -50 (Cum = 100, Trough = 100, Depth = 50 at 2026-08-05T10:00:00Z)
                    (
                        "4", "Turtle", "WIN", "WINQ26", "buy", "2026-08-05T09:00:00Z",
                        "2026-08-05T10:00:00Z", "closed", 1, 1, 100, 95, 0, 0, -50.0, 1, 1,
                    ),
                    # Day 9: +60 (Cum = 160 -> Recovered at 2026-08-09T10:00:00Z, dur = 6 days)
                    (
                        "5", "Turtle", "WIN", "WINQ26", "buy", "2026-08-09T09:00:00Z",
                        "2026-08-09T10:00:00Z", "closed", 1, 1, 100, 106, 0, 0, 60.0, 1, 1,
                    ),
                ],
            )

        response = self._request("/api/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Episode 1: Peak at 2026-08-01T10:00:00+00:00 (value 100), Trough -100, Rec 2026-08-03
        # Depth = 200, Duration = 2 days
        max_depth = data["max_depth_episode"]
        self.assertIsNotNone(max_depth)
        self.assertAlmostEqual(max_depth["depth"], 200.0)
        self.assertEqual(max_depth["peak_at"], "2026-08-01T10:00:00+00:00")
        self.assertAlmostEqual(max_depth["peak_value"], 100.0)
        self.assertEqual(max_depth["trough_at"], "2026-08-02T10:00:00+00:00")
        self.assertAlmostEqual(max_depth["trough_value"], -100.0)
        self.assertEqual(max_depth["recovery_at"], "2026-08-03T10:00:00+00:00")
        self.assertAlmostEqual(max_depth["duration_days"], 2.0)

        # Episode 2: Peak at 2026-08-03T10:00:00+00:00 (value 150), Trough 100, Rec 2026-08-09
        # Depth = 50, Duration = 6 days
        max_dur = data["max_duration_episode"]
        self.assertIsNotNone(max_dur)
        self.assertAlmostEqual(max_dur["depth"], 50.0)
        self.assertEqual(max_dur["peak_at"], "2026-08-03T10:00:00+00:00")
        self.assertAlmostEqual(max_dur["peak_value"], 150.0)
        self.assertEqual(max_dur["trough_at"], "2026-08-05T10:00:00+00:00")
        self.assertAlmostEqual(max_dur["trough_value"], 100.0)
        self.assertEqual(max_dur["recovery_at"], "2026-08-09T10:00:00+00:00")
        self.assertAlmostEqual(max_dur["duration_days"], 6.0)

    def test_metrics_drawdown_unrecovered_measures_duration_to_sample_end(self) -> None:
        """Unrecovered drawdown returns recovery_at null and duration to sample end."""
        with self._projection() as connection:
            self._seed_import(connection, 1)
            insert_positions(
                connection,
                [
                    # Peak at 2026-08-01 (+100)
                    (
                        "1", "Turtle", "WIN", "WINQ26", "buy", "2026-08-01T10:00:00Z",
                        "2026-08-01T12:00:00Z", "closed", 1, 1, 100, 110, 0, 0, 100.0, 1, 1,
                    ),
                    # Drop at 2026-08-03 (-80, Cum = 20)
                    (
                        "2", "Turtle", "WIN", "WINQ26", "buy", "2026-08-03T10:00:00Z",
                        "2026-08-03T12:00:00Z", "closed", 1, 1, 100, 92, 0, 0, -80.0, 1, 1,
                    ),
                    # Last sample position at 2026-08-06 (+10, Cum = 30 < 100)
                    (
                        "3", "Turtle", "WIN", "WINQ26", "buy", "2026-08-06T10:00:00Z",
                        "2026-08-06T12:00:00Z", "closed", 1, 1, 100, 101, 0, 0, 10.0, 1, 1,
                    ),
                ],
            )

        response = self._request("/api/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        max_depth = data["max_depth_episode"]
        self.assertIsNotNone(max_depth)
        self.assertAlmostEqual(max_depth["depth"], 80.0)
        self.assertEqual(max_depth["peak_at"], "2026-08-01T12:00:00+00:00")
        self.assertAlmostEqual(max_depth["peak_value"], 100.0)
        self.assertEqual(max_depth["trough_at"], "2026-08-03T12:00:00+00:00")
        self.assertAlmostEqual(max_depth["trough_value"], 20.0)
        self.assertIsNone(max_depth["recovery_at"])
        # From 2026-08-01T12:00:00Z to 2026-08-06T12:00:00Z = 5 days
        self.assertAlmostEqual(max_depth["duration_days"], 5.0)

    def test_metrics_drawdown_aggregates_tied_exit_timestamps(self) -> None:
        """Positions closed at the exact same timestamp are aggregated before curve updates."""
        with self._projection() as connection:
            self._seed_import(connection, 1)
            insert_positions(
                connection,
                [
                    # Base peak: +100
                    (
                        "1", "Turtle", "WIN", "WINQ26", "buy", "2026-08-01T10:00:00Z",
                        "2026-08-01T12:00:00Z", "closed", 1, 1, 100, 110, 0, 0, 100.0, 1, 1,
                    ),
                    # Two positions tied at 2026-08-02T12:00:00Z: -30 and -20 -> net -50
                    (
                        "2", "Turtle", "WIN", "WINQ26", "buy", "2026-08-02T10:00:00Z",
                        "2026-08-02T12:00:00Z", "closed", 1, 1, 100, 97, 0, 0, -30.0, 1, 1,
                    ),
                    (
                        "3", "Turtle", "WIN", "WINQ26", "sell", "2026-08-02T10:00:00Z",
                        "2026-08-02T12:00:00Z", "closed", 1, 1, 100, 98, 0, 0, -20.0, 1, 1,
                    ),
                    # Recovery at 2026-08-04T12:00:00Z: +60 (Cum = 110)
                    (
                        "4", "Turtle", "WIN", "WINQ26", "buy", "2026-08-04T10:00:00Z",
                        "2026-08-04T12:00:00Z", "closed", 1, 1, 100, 106, 0, 0, 60.0, 1, 1,
                    ),
                ],
            )

        response = self._request("/api/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        max_depth = data["max_depth_episode"]
        self.assertIsNotNone(max_depth)
        self.assertAlmostEqual(max_depth["depth"], 50.0)
        self.assertEqual(max_depth["peak_at"], "2026-08-01T12:00:00+00:00")
        self.assertEqual(max_depth["trough_at"], "2026-08-02T12:00:00+00:00")
        self.assertAlmostEqual(max_depth["trough_value"], 50.0)
        self.assertEqual(max_depth["recovery_at"], "2026-08-04T12:00:00+00:00")
        self.assertAlmostEqual(max_depth["duration_days"], 3.0)

    def test_metrics_no_drawdown_reports_unavailable_reason(self) -> None:
        """Monotonically non-decreasing curve produces no drawdown episodes with explicit reason."""
        with self._projection() as connection:
            self._seed_import(connection, 1)
            insert_positions(
                connection,
                [
                    (
                        "1", "Turtle", "WIN", "WINQ26", "buy", "2026-08-01T10:00:00Z",
                        "2026-08-01T12:00:00Z", "closed", 1, 1, 100, 110, 0, 0, 100.0, 1, 1,
                    ),
                    (
                        "2", "Turtle", "WIN", "WINQ26", "buy", "2026-08-02T10:00:00Z",
                        "2026-08-02T12:00:00Z", "closed", 1, 1, 100, 105, 0, 0, 50.0, 1, 1,
                    ),
                ],
            )

        response = self._request("/api/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIsNone(data["max_depth_episode"])
        self.assertIsNone(data["max_duration_episode"])
        self.assertEqual(
            data["unavailable_reasons"]["max_depth_episode"], "no_drawdown"
        )
        self.assertEqual(
            data["unavailable_reasons"]["max_duration_episode"], "no_drawdown"
        )
