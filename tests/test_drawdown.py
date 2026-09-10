"""Public monetary episode calculation examples."""

import unittest
from datetime import date, datetime, timezone
from fractions import Fraction
from typing import cast

from algobotdash.metrics import (
    DrawdownEpisode,
    calculate_monetary_drawdown,
    calculate_percentage_drawdown,
)


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

    def _check_unrecovered_index(self, loss: float, expected: float) -> None:
        """Check a two-day index path with a fixed second-day positive return."""
        result, reason = calculate_percentage_drawdown(
            ((datetime(2026, 8, 3, 12, tzinfo=timezone.utc), loss),
             (datetime(2026, 8, 4, 12, tzinfo=timezone.utc), 50)),
            {date(2026, 8, 3): 100, date(2026, 8, 4): 100},
            period=(date(2026, 8, 3), date(2026, 8, 7)),
        )
        self.assertIsNone(reason)
        self.assertIsNotNone(result["deepest_episode"])
        episode = cast(DrawdownEpisode, result["deepest_episode"])
        self.assertEqual(episode["depth"], expected)
        self.assertIsNone(episode["recovery_at"])

    def test_zero_index_stays_zero(self):
        """A total loss remains unrecovered after a positive daily return."""
        self._check_unrecovered_index(-100, -1.0)

    def test_negative_index_continues_signed_product(self):
        """A loss exceeding capital is neither clamped nor reset."""
        self._check_unrecovered_index(-150, -1.75)

    def _check_invalid_coverage(self, balance: float | None) -> None:
        """Assert the public unavailable contract for one invalid balance."""
        event = ((datetime.fromisoformat("2026-08-03T12:00:00+00:00"), -10),)
        period = (date(2026, 8, 3), date(2026, 8, 3))
        result, reason = calculate_percentage_drawdown(
            event, {period[0]: balance}, period=period,
        )
        self.assertEqual(result, {
            "state": "unavailable", "deepest_episode": None, "longest_episode": None,
        })
        self.assertEqual(reason, "invalid_opening_balance_coverage")

    def test_missing_opening_balance_is_unavailable(self):
        """A return cannot be calculated without its opening balance."""
        self._check_invalid_coverage(None)

    def test_zero_opening_balance_is_unavailable(self):
        """A zero denominator is not a valid performance reference."""
        self._check_invalid_coverage(0)

    def test_negative_opening_balance_is_unavailable(self):
        """A negative denominator is not a valid performance reference."""
        self._check_invalid_coverage(-10)

    def test_coverage_states_and_single_observation(self):
        """A single valid loss needs neither two observations nor annualization."""
        event = ((datetime.fromisoformat("2026-08-03T12:00:00+00:00"), -10),)
        period = (date(2026, 8, 3), date(2026, 8, 3))
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
                # Deliberately invalid input: the calculation must reject a naive date.
                ((datetime(2026, 8, 3, tzinfo=None), -10),), {},  # noqa: DTZ001
                period=(date(2026, 8, 3), date(2026, 8, 4)),
            )

    def _check_nonfinite_pnl(self, value: float) -> None:
        """Assert the stable public reason for one non-finite source P&L."""
        result, reason = calculate_percentage_drawdown(
            ((datetime(2026, 8, 3, 12, tzinfo=timezone.utc), value),),
            {date(2026, 8, 3): 100},
            period=(date(2026, 8, 3), date(2026, 8, 3)),
        )
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(reason, "numeric_overflow")

    def test_nan_pnl_is_unavailable(self):
        """NaN cannot become part of a linked performance index."""
        self._check_nonfinite_pnl(float("nan"))

    def test_positive_infinite_pnl_is_unavailable(self):
        """Positive infinity cannot become part of a linked performance index."""
        self._check_nonfinite_pnl(float("inf"))

    def test_negative_infinite_pnl_is_unavailable(self):
        """Negative infinity cannot become part of a linked performance index."""
        self._check_nonfinite_pnl(-float("inf"))

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
        self.assertIsNotNone(result["deepest_episode"])
        episode = cast(DrawdownEpisode, result["deepest_episode"])
        self.assertEqual(episode["valley_at"], "2026-08-05T12:00:00Z")

    def test_equal_percentage_episodes_keep_first_depth_and_duration(self):
        """Equal percentage episodes retain the first one for both selectors."""
        result, reason = calculate_percentage_drawdown(
            (
                (datetime(2026, 8, 3, 12, tzinfo=timezone.utc), -20),
                (datetime(2026, 8, 5, 12, tzinfo=timezone.utc), 50),
                (datetime(2026, 8, 6, 12, tzinfo=timezone.utc), -20),
                (datetime(2026, 8, 7, 12, tzinfo=timezone.utc), 25),
            ),
            {
                date(2026, 8, 3): 100,
                date(2026, 8, 5): 100,
                date(2026, 8, 6): 100,
                date(2026, 8, 7): 100,
            },
            period=(date(2026, 8, 3), date(2026, 8, 7)),
        )
        expected = {
            "depth": -0.2,
            "peak_at": "2026-08-03T03:00:00Z",
            "valley_at": "2026-08-03T12:00:00Z",
            "recovery_at": "2026-08-05T12:00:00Z",
            "duration_days": 2,
        }
        self.assertIsNone(reason)
        self.assertEqual(result["deepest_episode"], expected)
        self.assertEqual(result["longest_episode"], expected)

    def test_same_day_mixed_returns_produce_no_drawdown(self):
        """Opposite P&L events on one day are aggregated before index linking."""
        result, reason = calculate_percentage_drawdown(
            (
                (datetime(2026, 8, 3, 12, tzinfo=timezone.utc), -20),
                (datetime(2026, 8, 3, 17, tzinfo=timezone.utc), 20),
            ),
            {date(2026, 8, 3): 100},
            period=(date(2026, 8, 3), date(2026, 8, 3)),
        )
        self.assertEqual(result, {
            "state": "no_drawdown", "deepest_episode": None, "longest_episode": None,
        })
        self.assertIsNone(reason)

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

    def _expect_naive_monetary_timestamp_rejected(
        self, events: tuple[tuple[datetime, float], ...],
    ) -> None:
        """Assert that any ambiguous event timestamp is rejected at the public seam."""
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            calculate_monetary_drawdown(
                events, period=(date(2026, 8, 1), date(2026, 8, 2))
            )

    def test_first_naive_event_timestamp_is_rejected(self):
        """The first event must have an explicit timezone."""
        aware = datetime.fromisoformat("2026-08-01T12:00:00+00:00")
        self._expect_naive_monetary_timestamp_rejected(
            ((aware.replace(tzinfo=None), 10.0), (aware, -5.0), (aware, 5.0))
        )

    def test_middle_naive_event_timestamp_is_rejected(self):
        """The middle event must have an explicit timezone."""
        aware = datetime.fromisoformat("2026-08-01T12:00:00+00:00")
        self._expect_naive_monetary_timestamp_rejected(
            ((aware, 10.0), (aware.replace(tzinfo=None), -5.0), (aware, 5.0))
        )

    def test_last_naive_event_timestamp_is_rejected(self):
        """The final event must have an explicit timezone."""
        aware = datetime.fromisoformat("2026-08-01T12:00:00+00:00")
        self._expect_naive_monetary_timestamp_rejected(
            ((aware, 10.0), (aware, -5.0), (aware.replace(tzinfo=None), 5.0))
        )

    def test_only_naive_event_timestamp_is_rejected(self):
        """A single ambiguous event cannot be used to form an episode."""
        aware = datetime.fromisoformat("2026-08-01T12:00:00+00:00")
        self._expect_naive_monetary_timestamp_rejected(
            ((aware.replace(tzinfo=None), 10.0),)
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
        self.assertIsNotNone(result["deepest_episode"])
        episode = cast(DrawdownEpisode, result["deepest_episode"])
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
        self.assertIsNotNone(result["deepest_episode"])
        episode = cast(DrawdownEpisode, result["deepest_episode"])
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
