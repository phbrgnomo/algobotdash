"""Calendar P&L series over the shared, realized analytical sample."""

from __future__ import annotations

import calendar
import json
import math
from datetime import date, timedelta
from fractions import Fraction
from typing import Any
from zoneinfo import ZoneInfo

from .metrics import calculate_drawdown_series, effective_metric_period
from .storage import MetricSample, PositionFilters, SampleTooLargeError

MAX_SERIES_ROWS = 50_000
MAX_SERIES_POINTS = 50_000


def _calendar_bounds(day: date, granularity: str) -> tuple[date, date]:
    """Resolve civil buckets without assuming UTC days or fixed month lengths."""
    if granularity == "day":
        return day, day
    if granularity == "week":
        start = day - timedelta(days=min(day.weekday(), day.toordinal() - 1))
        return start, date.fromordinal(min(start.toordinal() + 6, date.max.toordinal()))
    if granularity == "month":
        return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])
    if granularity == "year":
        return date(day.year, 1, 1), date(day.year, 12, 31)
    raise ValueError("invalid series granularity")


def _number(value: Fraction) -> float:
    """Serialize only finite results, retaining exact arithmetic until this point."""
    result = float(value)
    if not math.isfinite(result):
        raise OverflowError("non-finite chart series")
    return result


# The public payload joins period metadata, decomposed performance and risk curves.
# pylint: disable=too-many-locals
def calculate_chart_series(
    sample: MetricSample, filters: PositionFilters, analysis_timezone: ZoneInfo,
    granularity: str,
) -> dict[str, Any]:
    """Aggregate closed positions, never paginated rows or account adjustments."""
    if len(sample.series_positions) > MAX_SERIES_ROWS:
        raise SampleTooLargeError("series_sample_limit")
    period = effective_metric_period(
        sample.daily_pnl, date_from=filters.date_from, date_to=filters.date_to,
        global_first_closed_date=sample.global_first_closed_date,
        global_last_closed_date=sample.global_last_closed_date,
    )
    payload: dict[str, Any] = {
        "state": "available", "reason": None,
        "projection_revision": sample.projection_revision,
        "timezone": analysis_timezone.key, "granularity": granularity,
        "effective_date_from": period[0].isoformat() if period else None,
        "effective_date_to": period[1].isoformat() if period else None,
        "periods": [], "strategies": [],
        "drawdown": calculate_drawdown_series(
            sample.closed_events, sample.opening_balances, period=period,
            analysis_timezone=analysis_timezone, realized_available=filters.status != "open",
        ),
    }
    if filters.selection_empty:
        payload["state"] = "empty_selection"
        return payload
    if filters.status == "open":
        payload.update(state="unavailable", reason="realized_metrics_unavailable_for_open_status")
        return payload
    if not sample.series_positions or period is None:
        payload["state"] = "empty_sample"
        return payload
    grouped: dict[tuple[str, str | None], dict[date, Fraction]] = {}
    bounds: dict[date, tuple[date, date]] = {}
    for position in sample.series_positions:
        day = position.at.astimezone(analysis_timezone).date()
        start, end = _calendar_bounds(day, granularity)
        bounds[start] = (max(start, period[0]), min(end, period[1]))
        outcomes = grouped.setdefault((position.symbol_family, position.strategy), {})
        outcomes[start] = outcomes.get(start, Fraction(0)) + position.pnl
        if len(grouped) * len(bounds) > MAX_SERIES_POINTS:
            raise SampleTooLargeError("series_point_limit")
    bucket_starts = sorted(bounds)
    totals = dict.fromkeys(bucket_starts, Fraction(0))
    strategies = []
    try:
        for (symbol, strategy), outcomes in sorted(
            grouped.items(), key=lambda item: (item[0][0], item[0][1] or "")
        ):
            cumulative = Fraction(0)
            points = []
            for start in bucket_starts:
                pnl = outcomes.get(start, Fraction(0))
                cumulative += pnl
                totals[start] += pnl
                points.append({"pnl": _number(pnl), "cumulative": _number(cumulative)})
            strategies.append({
                "id": json.dumps([symbol, strategy], ensure_ascii=False),
                "symbol_family": symbol, "strategy": strategy,
                "label": f"{symbol} {strategy or 'Sem estratégia comprovada'}",
                "points": points,
            })
        cumulative = Fraction(0)
        periods = []
        for start in bucket_starts:
            cumulative += totals[start]
            clipped_start, clipped_end = bounds[start]
            periods.append({
                "start": clipped_start.isoformat(), "end": clipped_end.isoformat(),
                "partial": (clipped_start, clipped_end) != _calendar_bounds(start, granularity),
                "pnl": _number(totals[start]), "cumulative": _number(cumulative),
            })
    except (OverflowError, ValueError):
        payload.update(state="unavailable", reason="numeric_overflow")
        return payload
    payload.update(periods=periods, strategies=strategies)
    return payload
