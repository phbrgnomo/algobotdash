"""Calculate realized metrics over analytical-position outcomes."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from fractions import Fraction
from typing import TypedDict
from zoneinfo import ZoneInfo

METRIC_TIMEZONE = ZoneInfo("America/Bahia")


class DrawdownEpisode(TypedDict):
    """Public episode shared by depth and duration selectors."""

    depth: float
    peak_at: str
    valley_at: str
    recovery_at: str | None
    duration_days: int


class MonetaryDrawdown(TypedDict):
    """Monetary risk state; absent episodes are never fabricated."""

    state: str
    deepest_episode: DrawdownEpisode | None
    longest_episode: DrawdownEpisode | None


def _utc_iso(at: datetime) -> str:
    """Serialize episode instants using the public UTC contract."""
    return at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class _DrawdownValley:
    """Keep an active episode's earliest minimum and its original peak."""

    peak_at: datetime
    valley_at: datetime
    depth: Fraction

    def observe(self, at: datetime, depth: Fraction) -> None:
        """Retain the first occurrence when minima tie."""
        if depth < self.depth:
            self.valley_at, self.depth = at, depth

    def serialize(self, end: date, recovery_at: datetime | None = None) -> DrawdownEpisode:
        """Close duration at recovery or the effective period's final civil day."""
        return {
            "depth": float(self.depth),
            "peak_at": _utc_iso(self.peak_at),
            "valley_at": _utc_iso(self.valley_at),
            "recovery_at": _utc_iso(recovery_at) if recovery_at is not None else None,
            "duration_days": (end - self.peak_at.astimezone(METRIC_TIMEZONE).date()).days,
        }


def _monetary_depths(
    events: Sequence[tuple[datetime, float | Fraction]], start: date,
) -> Iterator[tuple[datetime, datetime, Fraction]]:
    """Yield exact depths against the earliest peak, rejecting numeric overflow."""
    peak_at = datetime.combine(start, time.min, METRIC_TIMEZONE)
    peak = cumulative = Fraction(0)
    for at, pnl in events:
        # Decimal text preserves monetary equality without rounding to cents.
        cumulative += Fraction(str(pnl))
        depth = cumulative - peak
        if not math.isfinite(float(cumulative)) or not math.isfinite(float(depth)):
            raise OverflowError("non-finite drawdown")
        yield at, peak_at, depth
        if cumulative > peak:
            peak, peak_at = cumulative, at


def _monetary_episodes(
    events: Sequence[tuple[datetime, float | Fraction]], period: tuple[date, date],
) -> list[DrawdownEpisode]:
    """Collect recovered episodes and finalize any open episode at the period end."""
    episodes: list[DrawdownEpisode] = []
    valley: _DrawdownValley | None = None
    for at, peak_at, depth in _monetary_depths(events, period[0]):
        if depth < 0:
            if valley is None:
                valley = _DrawdownValley(peak_at, at, depth)
            valley.observe(at, depth)
        elif valley is not None:
            episodes.append(valley.serialize(at.astimezone(METRIC_TIMEZONE).date(), at))
            valley = None
    if valley is not None:
        episodes.append(valley.serialize(period[1]))
    return episodes


def _empty_drawdown(state: str) -> MonetaryDrawdown:
    """Represent absent or unavailable episodes without fabricated selectors."""
    return {"state": state, "deepest_episode": None, "longest_episode": None}


def calculate_monetary_drawdown(
    events: Sequence[tuple[datetime, float | Fraction]],
    *,
    period: tuple[date, date] | None,
    realized_available: bool = True,
) -> MonetaryDrawdown:
    """Scan chronological, timestamp-aggregated P&L using civil-day duration."""
    if not realized_available:
        return _empty_drawdown("unavailable")
    if not events or period is None:
        return _empty_drawdown("empty_sample")
    try:
        episodes = _monetary_episodes(events, period)
    except (OverflowError, ValueError):
        return _empty_drawdown("unavailable")
    if not episodes:
        return _empty_drawdown("no_drawdown")
    return {
        "state": "available",
        "deepest_episode": min(episodes, key=lambda item: item["depth"]),
        "longest_episode": max(episodes, key=lambda item: item["duration_days"]),
    }

METRIC_NAMES = (
    "net_pnl",
    "gross_profit",
    "gross_loss",
    "winning_trades",
    "losing_trades",
    "win_rate",
    "profit_factor",
    "payoff",
    "expectancy",
    "sharpe_per_position",
    "sortino_per_position",
)
RATIO_NAMES = (
    "win_rate",
    "profit_factor",
    "payoff",
    "expectancy",
    "sharpe_per_position",
    "sortino_per_position",
)
TEMPORAL_METRIC_NAMES = (
    "sharpe_daily",
    "sortino_daily",
    "sharpe_annualized",
    "sortino_annualized",
)


def _finite_ratio(numerator: float, denominator: float) -> float | None:
    """Return a representable ratio, never an infinite JSON number."""
    if denominator == 0:
        return None
    try:
        result = numerator / denominator
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


def _unavailable_payload(
    sample_size: int,
    excluded_open_positions: int,
    reason: str,
) -> dict[str, object]:
    """Return one fully unavailable realized-metrics payload."""
    return {
        "sample_size": sample_size,
        "excluded_open_positions": excluded_open_positions,
        **dict.fromkeys(METRIC_NAMES),
        "unavailable_reasons": dict.fromkeys(METRIC_NAMES, reason),
    }


def _empty_payload(excluded_open_positions: int) -> dict[str, object]:
    """Keep additive values available for an empty realized sample."""
    return {
        "sample_size": 0,
        "excluded_open_positions": excluded_open_positions,
        "net_pnl": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "winning_trades": 0,
        "losing_trades": 0,
        **dict.fromkeys(RATIO_NAMES),
        "unavailable_reasons": dict.fromkeys(RATIO_NAMES, "empty_sample"),
    }


def _quality_ratios(
    wins: Sequence[float], losses: Sequence[float]
) -> tuple[float, float, float | None, float | None, dict[str, str]]:
    """Calculate gross outcomes, profit factor, and payoff."""
    gross_profit = math.fsum(wins)
    gross_loss = math.fsum(losses)
    unavailable: dict[str, str] = {}
    if not losses:
        unavailable["profit_factor"] = "no_losing_positions"
        unavailable["payoff"] = "no_losing_positions"
        return gross_profit, gross_loss, None, None, unavailable
    profit_factor = _finite_ratio(gross_profit, abs(gross_loss))
    if profit_factor is None:
        unavailable["profit_factor"] = "numeric_overflow"
    if not wins:
        unavailable["payoff"] = "no_winning_positions"
        return gross_profit, gross_loss, profit_factor, None, unavailable
    average_win = gross_profit / len(wins)
    average_loss = gross_loss / len(losses)
    payoff = _finite_ratio(average_win, abs(average_loss))
    if payoff is None:
        unavailable["payoff"] = "numeric_overflow"
    return (
        gross_profit,
        gross_loss,
        profit_factor,
        payoff,
        unavailable,
    )


def _distribution_ratios(
    pnl_values: Sequence[float], mean_pnl: float
) -> tuple[float | None, float | None, dict[str, str]]:
    """Calculate finite Sharpe and Sortino ratios for one position sample."""
    if len(pnl_values) < 2:
        return None, None, {
            "sharpe_per_position": "insufficient_sample",
            "sortino_per_position": "insufficient_sample",
        }
    unavailable: dict[str, str] = {}
    try:
        sample_deviation = statistics.stdev(pnl_values)
    except OverflowError:
        sample_deviation = None
        sharpe = None
        unavailable["sharpe_per_position"] = "numeric_overflow"
    else:
        sharpe = (
            _finite_ratio(mean_pnl, sample_deviation) if sample_deviation else None
        )
    if sample_deviation == 0:
        unavailable["sharpe_per_position"] = "zero_standard_deviation"
    elif sample_deviation is not None and sharpe is None:
        unavailable["sharpe_per_position"] = "numeric_overflow"
    downside_norm = math.hypot(*(min(value, 0) for value in pnl_values))
    downside_deviation = downside_norm / math.sqrt(len(pnl_values))
    sortino = (
        _finite_ratio(mean_pnl, downside_deviation) if downside_deviation else None
    )
    if not downside_deviation:
        unavailable["sortino_per_position"] = "zero_downside_deviation"
    elif sortino is None:
        unavailable["sortino_per_position"] = "numeric_overflow"
    return sharpe, sortino, unavailable


def calculate_position_metrics(
    pnl_values: Sequence[float],
    *,
    excluded_open_positions: int,
    realized_available: bool = True,
) -> dict[str, object]:
    """Return unrounded metrics for a realized position sample."""
    if not realized_available:
        return _unavailable_payload(
            len(pnl_values),
            excluded_open_positions,
            "realized_metrics_unavailable_for_open_status",
        )
    if not pnl_values:
        return _empty_payload(excluded_open_positions)
    net_pnl = math.fsum(pnl_values)
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    gross_profit, gross_loss, profit_factor, payoff, quality_reasons = (
        _quality_ratios(wins, losses)
    )
    mean_pnl = net_pnl / len(pnl_values)
    sharpe, sortino, distribution_reasons = _distribution_ratios(
        pnl_values, mean_pnl
    )
    return {
        "sample_size": len(pnl_values),
        "excluded_open_positions": excluded_open_positions,
        "net_pnl": net_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": len(wins) / len(pnl_values),
        "profit_factor": profit_factor,
        "payoff": payoff,
        "expectancy": mean_pnl,
        "sharpe_per_position": sharpe,
        "sortino_per_position": sortino,
        "unavailable_reasons": quality_reasons | distribution_reasons,
    }


def _business_days(date_from: date, date_to: date) -> int:
    """Count weekdays in one inclusive interval without materializing it."""
    total_days = (date_to - date_from).days + 1
    full_weeks, remainder = divmod(total_days, 7)
    weekdays = full_weeks * 5
    return weekdays + sum(
        (date_from.weekday() + offset) % 7 < 5 for offset in range(remainder)
    )


def effective_metric_period(
    daily_pnl: Mapping[date, float],
    *,
    date_from: date | None,
    date_to: date | None,
    global_first_closed_date: date | None,
    global_last_closed_date: date | None,
) -> tuple[date, date] | None:
    """Resolve explicit, filtered, and global interval bounds."""
    selected_first = min(daily_pnl, default=None)
    selected_last = max(daily_pnl, default=None)
    if date_from is not None and date_to is not None:
        effective = (date_from, date_to)
    elif selected_first is not None and selected_last is not None:
        effective = (date_from or selected_first, date_to or selected_last)
    elif date_from is not None:
        if global_last_closed_date is None:
            return None
        effective = (date_from, global_last_closed_date)
    elif date_to is not None:
        if global_first_closed_date is None:
            return None
        effective = (global_first_closed_date, date_to)
    else:
        return None
    return None if effective[0] > effective[1] else effective


# Four related metric results keep this calculation cohesive.
# pylint: disable=too-many-locals
def _temporal_ratio_values(
    returns: Sequence[float], observation_days: int
) -> tuple[dict[str, float | None], dict[str, str]]:
    """Calculate daily and annualized ratios with zero-filled weekdays."""
    values: dict[str, float | None] = dict.fromkeys(TEMPORAL_METRIC_NAMES)
    reasons: dict[str, str] = {}
    if observation_days < 2:
        reasons["sharpe_daily"] = "insufficient_daily_sample"
        reasons["sortino_daily"] = "insufficient_daily_sample"
        daily_sharpe = None
        daily_sortino = None
    else:
        total = math.fsum(returns)
        mean = total / observation_days
        zero_days = observation_days - len(returns)
        deviation_norm = math.hypot(
            math.hypot(*(value - mean for value in returns)),
            abs(mean) * math.sqrt(zero_days),
        )
        deviation = deviation_norm / math.sqrt(observation_days - 1)
        downside = math.hypot(*(min(value, 0.0) for value in returns)) / math.sqrt(
            observation_days
        )
        daily_sharpe = (
            _finite_ratio(mean, deviation) if math.isfinite(deviation) else None
        )
        daily_sortino = (
            _finite_ratio(mean, downside) if math.isfinite(downside) else None
        )
        if deviation == 0:
            reasons["sharpe_daily"] = "zero_standard_deviation"
        elif not math.isfinite(deviation) or daily_sharpe is None:
            reasons["sharpe_daily"] = "numeric_overflow"
        if downside == 0:
            reasons["sortino_daily"] = "zero_downside_deviation"
        elif not math.isfinite(downside) or daily_sortino is None:
            reasons["sortino_daily"] = "numeric_overflow"
    values["sharpe_daily"] = daily_sharpe
    values["sortino_daily"] = daily_sortino
    for name, daily_value in (
        ("sharpe_annualized", daily_sharpe),
        ("sortino_annualized", daily_sortino),
    ):
        daily_name = name.removesuffix("_annualized") + "_daily"
        if observation_days < 30:
            reasons[name] = "insufficient_annualized_sample"
        elif daily_value is None:
            reasons[name] = reasons[daily_name]
        else:
            values[name] = _finite_ratio(daily_value * math.sqrt(252), 1.0)
            if values[name] is None:
                reasons[name] = "numeric_overflow"
    return values, reasons
# pylint: enable=too-many-locals


def _unavailable_temporal_payload(
    payload: dict[str, object], reason: str
) -> dict[str, object]:
    """Add unavailable temporal metrics with one shared reason."""
    return (
        payload
        | dict.fromkeys(TEMPORAL_METRIC_NAMES)
        | {
            "temporal_unavailable_reasons": dict.fromkeys(
                TEMPORAL_METRIC_NAMES, reason
            )
        }
    )


@dataclass(frozen=True)
class _TemporalCoverage:
    """Effective period and opening-balance coverage for a filtered sample."""

    period: tuple[date, date] | None
    observation_days: int
    required_dates: tuple[date, ...]
    missing_dates: tuple[date, ...]
    non_positive_dates: tuple[date, ...]


def _temporal_coverage(
    daily_pnl: Mapping[date, float],
    opening_balances: Mapping[date, float | None],
    period: tuple[date, date] | None,
) -> _TemporalCoverage:
    """Measure required opening balances inside the effective period."""
    if period is None:
        return _TemporalCoverage(None, 0, (), (), ())
    effective_from, effective_to = period
    required_dates = tuple(
        sorted(
            day
            for day in daily_pnl
            if day.weekday() < 5 and effective_from <= day <= effective_to
        )
    )
    missing_dates = tuple(
        day
        for day in required_dates
        if day not in opening_balances or opening_balances[day] is None
    )
    non_positive_dates = tuple(
        day
        for day in required_dates
        if (opening_balance := opening_balances.get(day)) is not None
        and opening_balance <= 0
    )
    return _TemporalCoverage(
        period,
        _business_days(effective_from, effective_to),
        required_dates,
        missing_dates,
        non_positive_dates,
    )


def _temporal_coverage_payload(coverage: _TemporalCoverage) -> dict[str, object]:
    """Serialize effective-period and opening-balance audit fields."""
    effective_from, effective_to = coverage.period or (None, None)
    invalid_days = len(coverage.missing_dates) + len(coverage.non_positive_dates)
    return {
        "effective_date_from": effective_from.isoformat() if effective_from else None,
        "effective_date_to": effective_to.isoformat() if effective_to else None,
        "daily_observation_days": coverage.observation_days,
        "opening_balance_required_days": len(coverage.required_dates),
        "opening_balance_covered_days": len(coverage.required_dates) - invalid_days,
        "opening_balance_missing_days": len(coverage.missing_dates),
        "opening_balance_missing_dates": [
            day.isoformat() for day in coverage.missing_dates
        ],
        "opening_balance_non_positive_days": len(coverage.non_positive_dates),
        "opening_balance_non_positive_dates": [
            day.isoformat() for day in coverage.non_positive_dates
        ],
    }


def _daily_returns(
    daily_pnl: Mapping[date, float],
    opening_balances: Mapping[date, float | None],
    required_dates: Sequence[date],
) -> list[float]:
    """Calculate returns after coverage has been validated."""
    returns: list[float] = []
    for day in required_dates:
        opening_balance = opening_balances[day]
        if opening_balance is None:
            raise AssertionError("saldo coberto sem valor")
        returns.append(daily_pnl[day] / opening_balance)
    return returns


def _safe_temporal_ratio_values(
    returns: Sequence[float], observation_days: int
) -> tuple[dict[str, float | None], dict[str, str]]:
    """Convert numeric failures into the stable public unavailability reason."""
    if not all(math.isfinite(value) for value in returns):
        return (
            dict.fromkeys(TEMPORAL_METRIC_NAMES),
            dict.fromkeys(TEMPORAL_METRIC_NAMES, "numeric_overflow"),
        )
    try:
        return _temporal_ratio_values(returns, observation_days)
    except (OverflowError, ValueError):
        return (
            dict.fromkeys(TEMPORAL_METRIC_NAMES),
            dict.fromkeys(TEMPORAL_METRIC_NAMES, "numeric_overflow"),
        )


# The public calculation seam receives each independently testable input.
# pylint: disable=too-many-arguments
def calculate_temporal_metrics(
    daily_pnl: Mapping[date, float],
    opening_balances: Mapping[date, float | None],
    *,
    date_from: date | None,
    date_to: date | None,
    global_first_closed_date: date | None,
    global_last_closed_date: date | None,
    realized_available: bool = True,
) -> dict[str, object]:
    """Return audited daily and annualized ratios for one filtered sample."""
    period = effective_metric_period(
        daily_pnl,
        date_from=date_from,
        date_to=date_to,
        global_first_closed_date=global_first_closed_date,
        global_last_closed_date=global_last_closed_date,
    )
    coverage = _temporal_coverage(daily_pnl, opening_balances, period)
    payload = _temporal_coverage_payload(coverage)
    if not realized_available:
        return _unavailable_temporal_payload(
            payload, "realized_metrics_unavailable_for_open_status"
        )
    if period is None:
        return _unavailable_temporal_payload(payload, "empty_sample")
    if coverage.missing_dates or coverage.non_positive_dates:
        return _unavailable_temporal_payload(
            payload, "invalid_opening_balance_coverage"
        )
    returns = _daily_returns(daily_pnl, opening_balances, coverage.required_dates)
    values, reasons = _safe_temporal_ratio_values(returns, coverage.observation_days)
    payload |= values
    payload["temporal_unavailable_reasons"] = reasons
    return payload
# pylint: enable=too-many-arguments
