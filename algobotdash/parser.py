from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook

from .config import ImportConfig


@dataclass(frozen=True)
class PositionRecord:
    position_id: str
    entry_at: datetime
    exit_at: datetime | None
    symbol_raw: str
    symbol_family: str | None
    direction: str
    volume: float | None
    entry_price: float | None
    exit_price: float | None
    commission: float
    swap: float
    pnl: float
    comment: str
    strategy: str | None
    status: str


@dataclass(frozen=True)
class RejectedRecord:
    row_number: int
    reason: str
    raw_position_id: str


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("/", "").strip())
    except (TypeError, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            pass
    return None


def _section_rows(rows: list[tuple[Any, ...]], section: str, next_section: str) -> Iterable[tuple[int, tuple[Any, ...]]]:
    start = next(i for i, row in enumerate(rows) if row and row[0] == section)
    end = next((i for i in range(start + 1, len(rows)) if rows[i] and rows[i][0] == next_section), len(rows))
    for index in range(start + 2, end):
        yield index + 1, rows[index]


def read_positions(source: str | Path, config: ImportConfig) -> tuple[list[PositionRecord], list[RejectedRecord]]:
    workbook = load_workbook(source, read_only=True, data_only=True)
    rows = list(workbook.active.iter_rows(values_only=True))
    try:
        comments = {
            str(row[1]): str(row[11] or "").strip()
            for _, row in _section_rows(rows, "Ordens", "Transações")
            if len(row) > 11 and row[1]
        }
        position_rows = _section_rows(rows, "Posições", "Ordens")
    except StopIteration as exc:
        raise ValueError("o workbook precisa conter as seções Posições e Ordens") from exc

    records: list[PositionRecord] = []
    rejected: list[RejectedRecord] = []
    for row_number, row in position_rows:
        position_id = str(row[1] or "").strip() if len(row) > 1 else ""
        symbol = str(row[2] or "").strip() if len(row) > 2 else ""
        if not any(value not in (None, "") for value in row):
            continue
        entry_at = _datetime(row[0] if row else None)
        pnl = _number(row[12] if len(row) > 12 else None)
        if not position_id or not entry_at or not symbol or pnl is None:
            rejected.append(RejectedRecord(row_number, "campos obrigatórios inválidos", position_id))
            continue
        comment = comments.get(position_id, "")
        strategy = config.classify_strategy(comment)
        records.append(
            PositionRecord(
                position_id=position_id,
                entry_at=entry_at,
                exit_at=_datetime(row[8] if len(row) > 8 else None),
                symbol_raw=symbol,
                symbol_family=config.normalize_symbol(symbol),
                direction=str(row[3] or "").strip().lower(),
                volume=_number(row[4] if len(row) > 4 else None),
                entry_price=_number(row[5] if len(row) > 5 else None),
                exit_price=_number(row[9] if len(row) > 9 else None),
                commission=_number(row[10] if len(row) > 10 else None) or 0.0,
                swap=_number(row[11] if len(row) > 11 else None) or 0.0,
                pnl=pnl,
                comment=comment,
                strategy=strategy,
                status="closed" if _datetime(row[8] if len(row) > 8 else None) else "open",
            )
        )
    return records, rejected
