"""Calculate realized metrics over analytical-position outcomes."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

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
