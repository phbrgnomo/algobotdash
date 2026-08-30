from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from .config import ImportConfig

REPORT_TZ = ZoneInfo("America/Bahia")


@dataclass(frozen=True)
class PositionRecord:
    position_id: str
    entry_at: datetime
    exit_at: datetime | None
    symbol_raw: str
    symbol_family: str | None
    direction: str
    volume_requested: float | None
    volume_executed: float | None
    entry_price: float | None
    exit_price: float | None
    commission: float
    swap: float
    pnl: float
    comment: str
    strategy: str | None
    status: str


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    opened_at: datetime
    symbol_raw: str
    direction: str
    volume_requested: float | None
    volume_executed: float | None
    price: float | None
    stop_loss: float | None
    take_profit: float | None
    event_at: datetime | None
    status: str
    comment: str
    position_id: str | None
    strategy: str | None


@dataclass(frozen=True)
class TransactionRecord:
    transaction_id: str
    at: datetime
    symbol_raw: str
    direction: str
    volume: float | None
    price: float | None
    order_id: str | None
    commission: float
    tax: float
    swap: float
    pnl: float
    balance: float | None
    comment: str
    position_id: str | None
    strategy: str | None


@dataclass(frozen=True)
class RejectedRecord:
    row_number: int
    reason: str
    raw_position_id: str


def _cell(row: tuple[Any, ...], index: int) -> Any:
    return row[index] if index < len(row) else None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _volume_pair(value: Any) -> tuple[float | None, float | None]:
    """Return requested and executed quantities without discarding either value."""
    if value is None or value == "":
        return None, None
    parts = [part.strip() for part in str(value).split("/")]
    if len(parts) == 1:
        amount = _number(parts[0])
        return amount, amount
    return _number(parts[0]), _number(parts[1])


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=REPORT_TZ)
    if not value:
        return None
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            return datetime.strptime(str(value).strip(), fmt).replace(tzinfo=REPORT_TZ)
        except ValueError:
            continue
    return None


def _section_rows(rows: list[tuple[Any, ...]], section: str, next_section: str) -> Iterable[tuple[int, tuple[Any, ...]]]:
    start = next(i for i, row in enumerate(rows) if _cell(row, 0) == section)
    end = next((i for i in range(start + 1, len(rows)) if _cell(rows[i], 0) == next_section), len(rows))
    for index in range(start + 2, end):
        yield index + 1, rows[index]


def _last_section_rows(rows: list[tuple[Any, ...]], section: str, end_marker: str) -> Iterable[tuple[int, tuple[Any, ...]]]:
    start = next(i for i, row in enumerate(rows) if _cell(row, 0) == section)
    end = next((i for i in range(start + 1, len(rows)) if _cell(rows[i], 0) == end_marker), len(rows))
    for index in range(start + 2, end):
        yield index + 1, rows[index]


def _read_rows(source: str | Path) -> list[tuple[Any, ...]]:
    workbook = load_workbook(source, read_only=True, data_only=True)
    sheet = workbook.active
    if sheet is None:
        raise ValueError(f"o workbook não possui planilha ativa: {source}")
    return [tuple(row) for row in sheet.iter_rows(values_only=True)]


def _report_sections(rows: list[tuple[Any, ...]]) -> tuple[list[tuple[int, tuple[Any, ...]]], list[tuple[int, tuple[Any, ...]]], list[tuple[int, tuple[Any, ...]]]]:
    try:
        order_rows = list(_section_rows(rows, "Ordens", "Transações"))
        position_rows = list(_section_rows(rows, "Posições", "Ordens"))
        transaction_rows = list(_last_section_rows(rows, "Transações", "Posições Abertas"))
    except StopIteration as exc:
        raise ValueError("o workbook precisa conter as seções Posições e Ordens") from exc
    return position_rows, order_rows, transaction_rows


def _parse_positions(rows: Iterable[tuple[int, tuple[Any, ...]]], config: ImportConfig, comments: dict[str, str]) -> tuple[list[PositionRecord], set[str], list[RejectedRecord]]:
    records: list[PositionRecord] = []
    position_ids: set[str] = set()
    rejected: list[RejectedRecord] = []
    for row_number, row in rows:
        position_id = str(_cell(row, 1) or "").strip()
        symbol = str(_cell(row, 2) or "").strip()
        if all(value in (None, "") for value in row):
            continue
        entry_at = _datetime(_cell(row, 0))
        pnl = _number(_cell(row, 12))
        if not position_id or not entry_at or not symbol or pnl is None:
            rejected.append(RejectedRecord(row_number, "campos obrigatórios inválidos", position_id))
            continue
        comment = comments.get(position_id, "")
        strategy = config.classify_strategy(comment)
        volume_requested, volume_executed = _volume_pair(_cell(row, 4))
        exit_at = _datetime(_cell(row, 8))
        records.append(PositionRecord(position_id, entry_at, exit_at, symbol, config.normalize_symbol(symbol), str(_cell(row, 3) or "").strip().lower(), volume_requested, volume_executed, _number(_cell(row, 5)), _number(_cell(row, 9)), _number(_cell(row, 10)) or 0.0, _number(_cell(row, 11)) or 0.0, pnl, comment, strategy, "closed" if exit_at else "open"))
        position_ids.add(position_id)
    return records, position_ids, rejected


def _parse_orders(rows: Iterable[tuple[int, tuple[Any, ...]]], config: ImportConfig, position_ids: set[str]) -> tuple[list[OrderRecord], list[RejectedRecord]]:
    orders: list[OrderRecord] = []
    rejected: list[RejectedRecord] = []
    for row_number, row in rows:
        order_id = str(_cell(row, 1) or "").strip()
        opened_at = _datetime(_cell(row, 0))
        symbol = str(_cell(row, 2) or "").strip()
        if not order_id or not opened_at or not symbol:
            if any(value not in (None, "") for value in row):
                rejected.append(RejectedRecord(row_number, "campos obrigatórios inválidos em ordem", order_id))
            continue
        requested, executed = _volume_pair(_cell(row, 4))
        comment = str(_cell(row, 11) or "").strip()
        orders.append(OrderRecord(order_id, opened_at, symbol, str(_cell(row, 3) or "").strip().lower(), requested, executed, _number(_cell(row, 5)), _number(_cell(row, 6)), _number(_cell(row, 7)), _datetime(_cell(row, 8)), str(_cell(row, 9) or "").strip().lower(), comment, order_id if order_id in position_ids else None, config.classify_strategy(comment)))
    return orders, rejected


def _parse_transactions(rows: Iterable[tuple[int, tuple[Any, ...]]], config: ImportConfig, comments: dict[str, str], position_ids: set[str]) -> tuple[list[TransactionRecord], list[RejectedRecord]]:
    transactions: list[TransactionRecord] = []
    rejected: list[RejectedRecord] = []
    for row_number, row in rows:
        transaction_id = str(_cell(row, 1) or "").strip()
        at = _datetime(_cell(row, 0))
        order_id = str(_cell(row, 7) or "").strip()
        if not transaction_id and not at:
            continue
        if not transaction_id or not at:
            if any(value not in (None, "") for value in row):
                rejected.append(RejectedRecord(row_number, "campos obrigatórios inválidos em transação", transaction_id))
            continue
        position_id = order_id if order_id in position_ids else None
        strategy = config.classify_strategy(comments.get(order_id, "")) if position_id else None
        transactions.append(TransactionRecord(transaction_id, at, str(_cell(row, 2) or "").strip(), str(_cell(row, 4) or _cell(row, 3) or "").strip().lower(), _number(_cell(row, 5)), _number(_cell(row, 6)), order_id or None, _number(_cell(row, 8)) or 0.0, _number(_cell(row, 9)) or 0.0, _number(_cell(row, 10)) or 0.0, _number(_cell(row, 11)) or 0.0, _number(_cell(row, 12)), str(_cell(row, 13) or "").strip(), position_id, strategy))
    return transactions, rejected


def read_report(source: str | Path, config: ImportConfig) -> tuple[list[PositionRecord], list[OrderRecord], list[TransactionRecord], list[RejectedRecord], int]:
    rows = _read_rows(source)
    position_rows, order_rows, transaction_rows = _report_sections(rows)
    comments = {str(_cell(row, 1)): str(_cell(row, 11) or "").strip() for _, row in order_rows if _cell(row, 1) not in (None, "")}
    positions, position_ids, rejected_positions = _parse_positions(position_rows, config, comments)
    orders, rejected_orders = _parse_orders(order_rows, config, position_ids)
    transactions, rejected_transactions = _parse_transactions(transaction_rows, config, comments, position_ids)
    rejected = rejected_positions + rejected_orders + rejected_transactions
    return positions, orders, transactions, rejected, len(position_rows) + len(order_rows) + len(transaction_rows)


def read_positions(source: str | Path, config: ImportConfig) -> tuple[list[PositionRecord], list[RejectedRecord]]:
    """Compatibility helper for callers that only need the position summary."""
    records, _, _, rejected, _ = read_report(source, config)
    return records, rejected
