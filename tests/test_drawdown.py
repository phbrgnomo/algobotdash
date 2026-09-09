"""Public monetary episode calculation examples."""

import unittest
from datetime import date, datetime, timezone
from fractions import Fraction

from algobotdash.metrics import calculate_monetary_drawdown, calculate_percentage_drawdown


class PercentageDrawdownTests(unittest.TestCase):
    """Exercise the public calculation with independently worked index paths."""

    def test_depth_and_duration_select_different_episodes(self):
        """100 -> 80 -> 100 -> 90 has a deeper recovered and longer open episode."""
        result, reason = calculate_percentage_drawdown(
            tuple((datetime.fromisoformat(at), pnl) for at, pnl in [
                ("2026-08-03T12:00:00+00:00", -20),
                ("2026-08-04T12:00:00+00:00", 25),
                ("2026-08-05T12:00:00+00:00", -10),
            ]),
            {date(2026, 8, day): 100 for day in (3, 4, 5)},
            period=(date(2026, 8, 3), date(2026, 8, 10)),
        )
        self.assertIsNone(reason)
        self.assertEqual(result["deepest_episode"], {
            "depth": -0.2, "peak_at": "2026-08-03T03:00:00Z",
            "valley_at": "2026-08-03T12:00:00Z",
            "recovery_at": "2026-08-04T12:00:00Z", "duration_days": 1,
        })
        self.assertEqual(result["longest_episode"], {
            "depth": -0.1, "peak_at": "2026-08-03T03:00:00Z",
            "valley_at": "2026-08-05T12:00:00Z",
            "recovery_at": None, "duration_days": 7,
        })

    def test_zero_and_negative_index_are_not_clamped_or_reset(self):
        """A zero index stays zero; a negative index continues the signed product."""
        for loss, expected in [(-100, -1.0), (-150, -1.75)]:
            with self.subTest(loss=loss):
                result, reason = calculate_percentage_drawdown(
                    ((datetime(2026, 8, 3, 12, tzinfo=timezone.utc), loss),
                     (datetime(2026, 8, 4, 12, tzinfo=timezone.utc), 50)),
                    {date(2026, 8, 3): 100, date(2026, 8, 4): 100},
                    period=(date(2026, 8, 3), date(2026, 8, 7)),
                )
                self.assertIsNone(reason)
                episode = result["deepest_episode"]
                assert episode is not None
                self.assertEqual(episode["depth"], expected)
                self.assertIsNone(episode["recovery_at"])

    def test_coverage_states_and_single_observation(self):
        """A single valid loss needs neither two observations nor annualization."""
        event = ((datetime.fromisoformat("2026-08-03T12:00:00+00:00"), -10),)
        period = (date(2026, 8, 3), date(2026, 8, 3))
        for balance in (None, 0, -10):
            with self.subTest(balance=balance):
                result, reason = calculate_percentage_drawdown(
                    event, {period[0]: balance}, period=period,
                )
                self.assertEqual(result, {
                    "state": "unavailable", "deepest_episode": None, "longest_episode": None,
                })
                self.assertEqual(reason, "invalid_opening_balance_coverage")
        valid, reason = calculate_percentage_drawdown(event, {period[0]: 100}, period=period)
        self.assertEqual(valid["state"], "available")
        self.assertIsNone(reason)
        empty, _ = calculate_percentage_drawdown((), {}, period=period)
        self.assertEqual(empty["state"], "empty_sample")
        opened, reason = calculate_percentage_drawdown(
            (), {}, period=period, realized_available=False,
        )
        self.assertEqual(opened["state"], "unavailable")
        self.assertEqual(reason, "realized_metrics_unavailable_for_open_status")

    def test_overflow_discards_already_recovered_episode(self):
        """A late product overflow cannot publish a partial risk history."""
        result, reason = calculate_percentage_drawdown(
            tuple((datetime(2026, 8, day, 12, tzinfo=timezone.utc), pnl)
                  for day, pnl in [(3, -20), (4, 25), (5, 1e200), (6, 1e200)]),
            {date(2026, 8, day): 100 for day in (3, 4, 5, 6)},
            period=(date(2026, 8, 3), date(2026, 8, 7)),
        )
        self.assertEqual(result, {
            "state": "unavailable", "deepest_episode": None, "longest_episode": None,
        })
        self.assertEqual(reason, "numeric_overflow")

    def test_weekends_and_outside_period_do_not_change_daily_index(self):
        """Excluded events require no balance and cannot fabricate a loss."""
        result, reason = calculate_percentage_drawdown(
            tuple((datetime(2026, 8, day, 12, tzinfo=timezone.utc), pnl)
                  for day, pnl in [(2, -500), (3, 10), (8, -500), (10, -500)]),
            {date(2026, 8, 3): 100},
            period=(date(2026, 8, 3), date(2026, 8, 9)),
        )
        self.assertEqual(result["state"], "no_drawdown")
        self.assertIsNone(reason)

    def test_rejects_reversed_period_and_naive_timestamp(self):
        """Invalid helper inputs cannot create misleading civil dates."""
        with self.assertRaisesRegex(ValueError, "period end"):
            calculate_percentage_drawdown(
                ((datetime(2026, 8, 3, tzinfo=timezone.utc), -10),), {},
                period=(date(2026, 8, 4), date(2026, 8, 3)),
            )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            calculate_percentage_drawdown(
                ((datetime(2026, 8, 3), -10),), {},
                period=(date(2026, 8, 3), date(2026, 8, 4)),
            )

    def test_nonfinite_pnl_is_unavailable(self):
        """The public calculation contains numeric failures as well as overflow."""
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                result, reason = calculate_percentage_drawdown(
                    ((datetime(2026, 8, 3, 12, tzinfo=timezone.utc), value),),
                    {date(2026, 8, 3): 100},
                    period=(date(2026, 8, 3), date(2026, 8, 3)),
                )
                self.assertEqual(result["state"], "unavailable")
                self.assertEqual(reason, "numeric_overflow")

    def test_deepest_selection_precedes_float_serialization(self):
        """Distinct exact depths that round to the same float are not ties."""
        result, _ = calculate_percentage_drawdown(
            tuple((datetime(2026, 8, day, 12, tzinfo=timezone.utc), pnl)
                  for day, pnl in [
                      (3, Fraction(-1, 10)), (4, Fraction(1, 9)),
                      (5, Fraction(-100000000000000001, 10**18)),
                  ]),
            {date(2026, 8, day): 1 for day in (3, 4, 5)},
            period=(date(2026, 8, 3), date(2026, 8, 5)),
        )
        episode = result["deepest_episode"]
        assert episode is not None
        self.assertEqual(episode["valley_at"], "2026-08-05T12:00:00Z")

    def test_daily_linking_uses_last_exit_and_recovers_exactly(self):
        """100 -> 70 -> 100 recovers even though 3/7 is a repeating return."""
        result, reason = calculate_percentage_drawdown(
            tuple((datetime.fromisoformat(at), pnl) for at, pnl in [
                ("2026-08-03T12:00:00+00:00", -10),
                ("2026-08-03T17:00:00+00:00", -20),
                ("2026-08-04T15:00:00+00:00", 30),
            ]),
            {date(2026, 8, 3): 100, date(2026, 8, 4): 70},
            period=(date(2026, 8, 3), date(2026, 8, 7)),
        )
        self.assertIsNone(reason)
        self.assertEqual(result, {
            "state": "available",
            "deepest_episode": {
                "depth": -0.3, "peak_at": "2026-08-03T03:00:00Z",
                "valley_at": "2026-08-03T17:00:00Z",
                "recovery_at": "2026-08-04T15:00:00Z", "duration_days": 1,
            },
            "longest_episode": {
                "depth": -0.3, "peak_at": "2026-08-03T03:00:00Z",
                "valley_at": "2026-08-03T17:00:00Z",
                "recovery_at": "2026-08-04T15:00:00Z", "duration_days": 1,
            },
        })


class DrawdownTests(unittest.TestCase):
    """Verify episodes against worked cumulative P&L paths."""

    def test_reversed_reporting_period_is_rejected(self):
        """An open loss must not produce a negative civil-day duration."""
        with self.assertRaisesRegex(ValueError, "period end must not precede period start"):
            calculate_monetary_drawdown(
                ((datetime.fromisoformat("2026-08-02T12:00:00+00:00"), -10.0),),
                period=(date(2026, 8, 2), date(2026, 8, 1)),
            )

    def test_naive_event_timestamps_are_rejected_before_calculation(self):
        """Reject ambiguous dates even when they would not serialize an episode."""
        aware = datetime.fromisoformat("2026-08-01T12:00:00+00:00")
        for naive_index in range(3):
            with self.subTest(naive_index=naive_index):
                events = tuple(
                    (aware.replace(tzinfo=None) if index == naive_index else aware, pnl)
                    for index, pnl in enumerate((10.0, -5.0, 5.0))
                )
                with self.assertRaisesRegex(ValueError, "timezone-aware"):
                    calculate_monetary_drawdown(
                        events, period=(date(2026, 8, 1), date(2026, 8, 2))
                    )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            calculate_monetary_drawdown(
                ((aware.replace(tzinfo=None), 10.0),),
                period=(date(2026, 8, 1), date(2026, 8, 2)),
            )

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
