"""Shared decimal monetary arithmetic for metrics and chart series."""

import math
from collections.abc import Iterable
from fractions import Fraction


def monetary_sum(values: Iterable[float]) -> float:
    """Sum decimal representations exactly, converting once at the JSON boundary."""
    result = float(sum((Fraction(str(value)) for value in values), Fraction(0)))
    if not math.isfinite(result):
        raise OverflowError("non-finite monetary sum")
    return result
