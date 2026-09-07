"""Public monetary episode calculation examples."""

import unittest
from datetime import date, datetime

from algobotdash.metrics import calculate_monetary_drawdown


class DrawdownTests(unittest.TestCase):
    """Verify episodes against worked cumulative P&L paths."""

    def test_equal_valleys_and_equal_episodes_keep_first_occurrence(self):
        """Repeated minima and equal selectors preserve chronological precedence."""
        result = calculate_monetary_drawdown(tuple(
            (datetime.fromisoformat(at), pnl) for at, pnl in [
                ("2026-08-01T12:00:00+00:00", 10),
                ("2026-08-02T12:00:00+00:00", -5),
                ("2026-08-02T13:00:00+00:00", 1),
                ("2026-08-02T14:00:00+00:00", -1),
                ("2026-08-03T12:00:00+00:00", 6),
                ("2026-08-04T12:00:00+00:00", -5),
                ("2026-08-05T12:00:00+00:00", 5),
            ]), period=(date(2026, 8, 1), date(2026, 8, 5)))
        self.assertEqual(result["deepest_episode"], {
            "depth": -5, "peak_at": "2026-08-01T12:00:00Z",
            "valley_at": "2026-08-02T12:00:00Z",
            "recovery_at": "2026-08-03T12:00:00Z", "duration_days": 2,
        })
        self.assertEqual(result["deepest_episode"], result["longest_episode"])

    def test_duration_counts_bahia_dates_not_utc_dates_or_elapsed_days(self):
        """Two minutes across local midnight count as one civil day."""
        result = calculate_monetary_drawdown(tuple(
            (datetime.fromisoformat(at), pnl) for at, pnl in [
                ("2026-08-02T02:59:00+00:00", 10),
                ("2026-08-02T03:00:00+00:00", -5),
                ("2026-08-02T03:01:00+00:00", 5),
            ]), period=(date(2026, 8, 1), date(2026, 8, 2)))
        episode = result["deepest_episode"]
        assert isinstance(episode, dict)
        self.assertEqual(episode.get("duration_days"), 1)

    def test_numeric_overflow_does_not_publish_partial_episodes(self):
        """Unrepresentable depth leaves both selectors empty."""
        result = calculate_monetary_drawdown((
            (datetime.fromisoformat("2026-08-01T12:00:00+00:00"), -1e308),
            (datetime.fromisoformat("2026-08-02T12:00:00+00:00"), -1e308),
        ), period=(date(2026, 8, 1), date(2026, 8, 2)))
        self.assertEqual(result, {
            "state": "unavailable", "deepest_episode": None, "longest_episode": None,
        })

    def test_decimal_losses_recover_without_binary_residue(self):
        """Ten plus twenty cents recover exactly with thirty cents."""
        result = calculate_monetary_drawdown(tuple(
            (datetime.fromisoformat(at), pnl) for at, pnl in [
                ("2026-08-01T12:00:00+00:00", -0.1),
                ("2026-08-02T12:00:00+00:00", -0.2),
                ("2026-08-03T12:00:00+00:00", 0.3),
            ]), period=(date(2026, 8, 1), date(2026, 8, 7)))
        episode = result["deepest_episode"]
        self.assertIsNotNone(episode)
        assert isinstance(episode, dict)
        self.assertEqual(episode.get("recovery_at"), "2026-08-03T12:00:00Z")
        self.assertEqual(episode.get("depth"), -0.3)

    def test_initial_loss_recovers_at_previous_peak(self):
        """Initial loss is anchored at the explicit period start."""
        result = calculate_monetary_drawdown(
            ((datetime.fromisoformat("2026-08-02T12:00:00+00:00"), -10.0),
             (datetime.fromisoformat("2026-08-04T12:00:00+00:00"), 10.0)),
            period=(date(2026, 8, 1), date(2026, 8, 7)),
        )
        self.assertEqual(result, {
            "state": "available",
            "deepest_episode": {
                "depth": -10.0, "peak_at": "2026-08-01T03:00:00Z",
                "valley_at": "2026-08-02T12:00:00Z",
                "recovery_at": "2026-08-04T12:00:00Z", "duration_days": 3,
            },
            "longest_episode": {
                "depth": -10.0, "peak_at": "2026-08-01T03:00:00Z",
                "valley_at": "2026-08-02T12:00:00Z",
                "recovery_at": "2026-08-04T12:00:00Z", "duration_days": 3,
            },
        })
