"""Persist import projections and their history in SQLite."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, TypeAlias
from zoneinfo import ZoneInfo

from .parser import OrderRecord, PositionRecord, RejectedRecord, TransactionRecord

ImportHistoryRow: TypeAlias = tuple[int, str, str, str, int, int, int, int]
ProjectionRow: TypeAlias = dict[str, Any]

POSITION_SORT_COLUMNS = {
    "opened_at": "utc_timestamp(entry_at)",
    "closed_at": "utc_timestamp(exit_at)",
    "realized_pnl": "CASE WHEN status = 'open' THEN NULL ELSE pnl END",
    "symbol_family": "symbol_family",
    "status": "status",
}
SORT_ORDERS = {"asc": "ASC", "desc": "DESC"}
REPORT_TZ = ZoneInfo("America/Bahia")

REQUIRED_TABLE_COLUMNS = {
    "imports": {
        "id", "source_name", "source_hash", "imported_at", "rows_read",
        "positions_created", "no_comment_count", "rejected_count",
    },
    "positions": {
        "id", "position_id", "strategy", "is_associated", "symbol_family",
        "symbol_raw", "direction",
        "entry_at", "exit_at", "status", "volume_requested", "volume_executed",
        "entry_price", "exit_price", "commission", "swap", "pnl", "import_id",
    },
    "orders": {
        "id", "order_id", "position_id", "strategy", "symbol_raw", "direction",
        "opened_at", "event_at", "status", "volume_requested", "volume_executed",
        "price", "stop_loss", "take_profit", "comment", "import_id",
    },
    "transactions": {
        "id", "transaction_id", "order_id", "position_id", "strategy", "at",
        "symbol_raw", "direction", "volume", "price", "commission", "tax", "swap",
        "pnl", "balance", "comment", "import_id",
    },
    "rejected_rows": {"id", "import_id", "row_number", "position_id", "reason"},
}


class ProjectionUnavailableError(RuntimeError):
    """Raised when the derived SQLite projection cannot be queried safely."""


@dataclass(frozen=True)
class PositionFilters:
    """Validated filters shared by position rows and their total count."""

    strategy: str | None = None
    symbol_family: str | None = None
    direction: str | None = None
    status: str = "closed"
    association: str = "all"
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True)
class ProjectionData:
    """Parsed records and counters required to build a projection."""

    positions: Iterable[PositionRecord]
    orders: Iterable[OrderRecord]
    transactions: Iterable[TransactionRecord]
    rejected: Iterable[RejectedRecord]
    rows_read: int

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE imports (
  id INTEGER PRIMARY KEY,
  source_name TEXT NOT NULL,
  source_hash TEXT NOT NULL UNIQUE,
  imported_at TEXT NOT NULL,
  rows_read INTEGER NOT NULL,
  positions_created INTEGER NOT NULL,
  no_comment_count INTEGER NOT NULL,
  rejected_count INTEGER NOT NULL
);
CREATE TABLE positions (
  id INTEGER PRIMARY KEY,
  position_id TEXT NOT NULL UNIQUE,
  strategy TEXT,
  is_associated INTEGER NOT NULL CHECK (is_associated IN (0, 1)),
  symbol_family TEXT,
  symbol_raw TEXT NOT NULL,
  direction TEXT NOT NULL,
  entry_at TEXT NOT NULL,
  exit_at TEXT,
  status TEXT NOT NULL,
  volume_requested REAL,
  volume_executed REAL,
  entry_price REAL,
  exit_price REAL,
  commission REAL NOT NULL,
  swap REAL NOT NULL,
  pnl REAL NOT NULL,
  import_id INTEGER NOT NULL REFERENCES imports(id)
);
CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  order_id TEXT NOT NULL UNIQUE,
  position_id TEXT REFERENCES positions(position_id),
  strategy TEXT,
  symbol_raw TEXT NOT NULL,
  direction TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  event_at TEXT,
  status TEXT NOT NULL,
  volume_requested REAL,
  volume_executed REAL,
  price REAL,
  stop_loss REAL,
  take_profit REAL,
  comment TEXT NOT NULL,
  import_id INTEGER NOT NULL REFERENCES imports(id)
);
CREATE TABLE transactions (
  id INTEGER PRIMARY KEY,
  transaction_id TEXT NOT NULL UNIQUE,
  order_id TEXT,
  position_id TEXT,
  strategy TEXT,
  at TEXT NOT NULL,
  symbol_raw TEXT NOT NULL,
  direction TEXT NOT NULL,
  volume REAL,
  price REAL,
  commission REAL NOT NULL,
  tax REAL NOT NULL,
  swap REAL NOT NULL,
  pnl REAL NOT NULL,
  balance REAL,
  comment TEXT NOT NULL,
  import_id INTEGER NOT NULL REFERENCES imports(id)
);
CREATE TABLE rejected_rows (
  id INTEGER PRIMARY KEY,
  import_id INTEGER NOT NULL REFERENCES imports(id),
  row_number INTEGER NOT NULL,
  position_id TEXT NOT NULL,
  reason TEXT NOT NULL
);
"""


def read_import_history(path: Path) -> list[ImportHistoryRow]:
    """Read and validate the import history from an existing database."""
    if not path.exists():
        return []
    connection = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "imports" not in tables:
            raise ValueError(f"schema SQLite incompatível: tabela imports ausente em {path}")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(imports)")}
        required = REQUIRED_TABLE_COLUMNS["imports"]
        if missing_columns := required - columns:
            raise ValueError(
                f"schema SQLite incompatível em {path}: colunas ausentes em imports: "
                f"{sorted(missing_columns)}"
            )
        history = connection.execute(
            "SELECT id, source_name, source_hash, imported_at, rows_read, "
            "positions_created, no_comment_count, rejected_count "
            "FROM imports ORDER BY id"
        ).fetchall()
        for row in history:
            try:
                _ = _utc_timestamp(row[3])
            except ProjectionUnavailableError as exc:
                raise ValueError(
                    f"schema SQLite incompatível em {path}: imported_at inválido"
                ) from exc
        return history
    finally:
        connection.close()


def _open_projection(path: Path) -> sqlite3.Connection:
    """Open a compatible projection in read-only mode or raise a stable error."""
    if not path.is_file():
        raise ProjectionUnavailableError("projeção SQLite indisponível")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "utc_timestamp", 1, _utc_timestamp, deterministic=True
        )
        connection.create_function(
            "bahia_date", 1, _bahia_date, deterministic=True
        )
        _validate_projection(connection)
        return connection
    except (OSError, sqlite3.Error, ValueError) as exc:
        if connection is not None:
            connection.close()
        raise ProjectionUnavailableError("projeção SQLite indisponível") from exc


def _validate_projection(connection: sqlite3.Connection) -> None:
    """Validate the required tables and columns of an open projection."""
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    required_tables = set(REQUIRED_TABLE_COLUMNS)
    if missing_tables := required_tables - tables:
        raise ValueError(f"tabelas ausentes: {sorted(missing_tables)}")
    for table, required_columns in REQUIRED_TABLE_COLUMNS.items():
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM pragma_table_info(?)",
                (table,),
            )
        }
        if missing_columns := required_columns - columns:
            raise ValueError(
                f"colunas ausentes em {table}: {sorted(missing_columns)}"
            )
# pylint: disable=too-many-arguments,too-many-positional-arguments
def _page_rows(
    connection: sqlite3.Connection,
    query: str,
    count_query: str,
    limit: int,
    offset: int,
    parameters: tuple[object, ...] = (),
) -> tuple[list[ProjectionRow], int]:
    """Return a page and its total from a read-only projection query."""
    try:
        total = connection.execute(count_query, parameters).fetchone()[0]
        rows = connection.execute(query, (*parameters, limit, offset)).fetchall()
    except sqlite3.Error as exc:
        raise ProjectionUnavailableError("projeção SQLite indisponível") from exc
    return [dict(row) for row in rows], total
# pylint: enable=too-many-arguments,too-many-positional-arguments


def _utc_timestamp(value: object) -> str | None:
    """Normalize an offset-aware persisted timestamp to ISO 8601 UTC."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProjectionUnavailableError("projeção SQLite indisponível")
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            raise ValueError("timestamp sem fuso horário")
    except ValueError as exc:
        raise ProjectionUnavailableError("projeção SQLite indisponível") from exc
    return timestamp.astimezone(timezone.utc).isoformat()


def _bahia_date(value: object) -> str | None:
    """Return the calendar date of an aware timestamp in America/Bahia."""
    normalized = _utc_timestamp(value)
    if normalized is None:
        return None
    return datetime.fromisoformat(normalized).astimezone(REPORT_TZ).date().isoformat()


def _normalize_timestamps(
    rows: list[ProjectionRow], *fields: str
) -> list[ProjectionRow]:
    """Normalize selected timestamp fields while keeping other row values intact."""
    for row in rows:
        for field in fields:
            row[field] = _utc_timestamp(row[field])
    return rows


# pylint: disable=too-many-arguments,too-many-locals
def read_metrics(
    path: Path, filters: PositionFilters | None = None
) -> dict[str, Any]:
    """Calculate realized performance metrics for analytical positions."""
    active_filters = filters or PositionFilters()
    where_sql, parameters = _position_where(active_filters)
    query = (
        f"SELECT status, pnl FROM positions {where_sql}"  # noqa: S608  # nosec B608
    )
    connection = _open_projection(path)
    try:
        try:
            rows = connection.execute(query, parameters).fetchall()
        except sqlite3.Error as exc:
            raise ProjectionUnavailableError("projeção SQLite indisponível") from exc
    finally:
        connection.close()

    is_open_only = active_filters.status == "open"
    if is_open_only:
        excluded_open = len(rows)
        closed_pnls: list[float] = []
    elif active_filters.status == "closed":
        excluded_open = 0
        closed_pnls = [float(row["pnl"]) for row in rows if row["pnl"] is not None]
    else:  # status == "all"
        excluded_open = sum(1 for row in rows if row["status"] == "open")
        closed_pnls = [
            float(row["pnl"])
            for row in rows
            if row["status"] == "closed" and row["pnl"] is not None
        ]

    return _compute_realized_metrics(
        closed_pnls,
        excluded_open_count=excluded_open,
        is_open_only=is_open_only,
    )


# pylint: disable=too-many-branches,too-many-statements
def _compute_realized_metrics(
    pnls: list[float],
    *,
    excluded_open_count: int = 0,
    is_open_only: bool = False,
) -> dict[str, Any]:
    """Compute summary and ratio metrics according to declared conventions."""
    unavailable_reasons: dict[str, str] = {}
    metric_keys = [
        "net_pnl",
        "gross_profit",
        "gross_loss",
        "win_rate",
        "profit_factor",
        "payoff",
        "expectancy",
        "position_sharpe",
        "position_sortino",
    ]

    if is_open_only:
        for key in metric_keys:
            unavailable_reasons[key] = "open_positions_only"
        return {
            "total_sample": 0,
            "excluded_open_count": excluded_open_count,
            "net_pnl": None,
            "gross_profit": None,
            "gross_loss": None,
            "win_rate": None,
            "profit_factor": None,
            "payoff": None,
            "expectancy": None,
            "position_sharpe": None,
            "position_sortino": None,
            "unavailable_reasons": unavailable_reasons,
        }

    n = len(pnls)
    if n == 0:
        for key in metric_keys:
            unavailable_reasons[key] = "empty_sample"
        return {
            "total_sample": 0,
            "excluded_open_count": excluded_open_count,
            "net_pnl": None,
            "gross_profit": None,
            "gross_loss": None,
            "win_rate": None,
            "profit_factor": None,
            "payoff": None,
            "expectancy": None,
            "position_sharpe": None,
            "position_sortino": None,
            "unavailable_reasons": unavailable_reasons,
        }

    net_pnl = sum(pnls)
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    win_rate = len(wins) / n
    expectancy = net_pnl / n

    if gross_loss > 0:
        profit_factor: float | None = gross_profit / gross_loss
    else:
        profit_factor = None
        unavailable_reasons["profit_factor"] = "zero_gross_loss"

    if wins and losses:
        avg_win = gross_profit / len(wins)
        avg_loss = gross_loss / len(losses)
        payoff: float | None = avg_win / avg_loss
    elif not wins and not losses:
        payoff = None
        unavailable_reasons["payoff"] = "no_wins_or_losses"
    elif not wins:
        payoff = None
        unavailable_reasons["payoff"] = "no_wins"
    else:
        payoff = None
        unavailable_reasons["payoff"] = "no_losses"

    if n < 2:
        position_sharpe: float | None = None
        unavailable_reasons["position_sharpe"] = "sample_too_small"
    else:
        variance = sum((x - expectancy) ** 2 for x in pnls) / (n - 1)
        std_dev = math.sqrt(variance)
        if std_dev == 0:
            position_sharpe = None
            unavailable_reasons["position_sharpe"] = "zero_dispersion"
        else:
            position_sharpe = expectancy / std_dev

    downside_variance = sum(min(0.0, x) ** 2 for x in pnls) / n
    downside_dev = math.sqrt(downside_variance)
    if downside_dev == 0:
        position_sortino: float | None = None
        unavailable_reasons["position_sortino"] = "zero_downside_deviation"
    else:
        position_sortino = expectancy / downside_dev

    return {
        "total_sample": n,
        "excluded_open_count": excluded_open_count,
        "net_pnl": net_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "payoff": payoff,
        "expectancy": expectancy,
        "position_sharpe": position_sharpe,
        "position_sortino": position_sortino,
        "unavailable_reasons": unavailable_reasons,
    }


def read_positions(
    path: Path,
    *,
    limit: int,
    offset: int,
    sort_by: str,
    sort_order: str,
    filters: PositionFilters | None = None,
) -> tuple[list[ProjectionRow], int]:
    """Read a deterministic page of analytical positions from the projection."""
    try:
        column = POSITION_SORT_COLUMNS[sort_by]
        order = SORT_ORDERS[sort_order]
    except KeyError as exc:
        raise ValueError("ordenação de posições inválida") from exc
    active_filters = filters or PositionFilters()
    where_sql, parameters = _position_where(active_filters)
    query = (  # Fixed predicates and allowlisted ordering; values stay parameterized.
        "SELECT position_id, strategy, symbol_family, "  # noqa: S608  # nosec B608
        "CASE WHEN strategy IS NOT NULL AND symbol_family IS NOT NULL "
        "THEN symbol_family || ' ' || strategy END AS strategy_key, "
        "CASE WHEN is_associated = 1 THEN 'associated' ELSE 'unassociated' "
        "END AS association, "
        "direction, entry_at AS opened_at, "
        "exit_at AS closed_at, status, "
        "CASE WHEN status = 'open' THEN NULL ELSE pnl END AS realized_pnl "
        f"FROM positions {where_sql} "
        f"ORDER BY {column} {order}, position_id ASC LIMIT ? OFFSET ?"
    )
    connection = _open_projection(path)
    try:
        rows, total = _page_rows(
            connection,
            query,
            f"SELECT COUNT(*) FROM positions {where_sql}",  # noqa: S608  # nosec B608
            limit,
            offset,
            parameters,
        )
        return _normalize_timestamps(rows, "opened_at", "closed_at"), total
    finally:
        connection.close()
# pylint: enable=too-many-arguments,too-many-locals


def _position_where(filters: PositionFilters) -> tuple[str, tuple[object, ...]]:
    """Build one parameterized predicate for position rows and count queries."""
    clauses: list[str] = []
    parameters: list[object] = []
    if filters.strategy is not None:
        clauses.append("strategy = ?")
        parameters.append(filters.strategy)
    if filters.symbol_family is not None:
        clauses.append("symbol_family = ?")
        parameters.append(filters.symbol_family)
    if filters.direction is not None:
        clauses.append("direction = ?")
        parameters.append(filters.direction)
    if filters.status != "all":
        clauses.append("status = ?")
        parameters.append(filters.status)
    if filters.association != "all":
        clauses.append("is_associated = ?")
        parameters.append(1 if filters.association == "associated" else 0)
    analytical_timestamp = "CASE WHEN status = 'open' THEN entry_at ELSE exit_at END"
    if filters.date_from is not None:
        clauses.append(f"bahia_date({analytical_timestamp}) >= ?")
        parameters.append(filters.date_from.isoformat())
    if filters.date_to is not None:
        clauses.append(f"bahia_date({analytical_timestamp}) <= ?")
        parameters.append(filters.date_to.isoformat())
    return ("WHERE " + " AND ".join(clauses) if clauses else "", tuple(parameters))


def read_filter_options(path: Path) -> dict[str, list[str]]:
    """Return observed strategy and symbol-family filter values."""
    connection = _open_projection(path)
    try:
        try:
            strategies = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT strategy FROM positions "
                    "WHERE strategy IS NOT NULL ORDER BY strategy"
                )
            ]
            symbol_families = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT symbol_family FROM positions "
                    "WHERE symbol_family IS NOT NULL ORDER BY symbol_family"
                )
            ]
        except sqlite3.Error as exc:
            raise ProjectionUnavailableError("projeção SQLite indisponível") from exc
        return {"strategies": strategies, "symbol_families": symbol_families}
    finally:
        connection.close()


def read_strategy_keys(path: Path) -> list[ProjectionRow]:
    """Return the symbol-qualified strategy identities present in the projection."""
    connection = _open_projection(path)
    try:
        try:
            rows = connection.execute(
                "SELECT symbol_family || ' ' || strategy AS strategy_key, "
                "strategy, symbol_family "
                "FROM positions WHERE strategy IS NOT NULL AND symbol_family IS NOT NULL "
                "GROUP BY symbol_family, strategy ORDER BY symbol_family, strategy"
            ).fetchall()
        except sqlite3.Error as exc:
            raise ProjectionUnavailableError("projeção SQLite indisponível") from exc
        return [dict(row) for row in rows]
    finally:
        connection.close()


def read_position_orders(path: Path, position_id: str) -> list[ProjectionRow] | None:
    """Read orders for a known report position identifier, if it exists."""
    connection = _open_projection(path)
    try:
        try:
            exists = connection.execute(
                "SELECT 1 FROM positions WHERE position_id = ?", (position_id,)
            ).fetchone()
            if exists is None:
                return None
            rows = connection.execute(
                "SELECT order_id, strategy, symbol_raw, direction, opened_at, event_at, "
                "status, volume_requested, volume_executed, price, stop_loss, "
                "take_profit, comment FROM orders WHERE position_id = ? "
                "ORDER BY opened_at ASC, order_id ASC",
                (position_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ProjectionUnavailableError("projeção SQLite indisponível") from exc
        return _normalize_timestamps(
            [dict(row) for row in rows], "opened_at", "event_at"
        )
    finally:
        connection.close()


def read_imports(
    path: Path, *, limit: int, offset: int
) -> tuple[list[ProjectionRow], int]:
    """Read valid import history in reverse chronological order."""
    query = (
        "SELECT id, source_name, source_hash, imported_at, rows_read, positions_created, "
        "no_comment_count, rejected_count FROM imports "
        "ORDER BY utc_timestamp(imported_at) DESC, id DESC LIMIT ? OFFSET ?"
    )
    connection = _open_projection(path)
    try:
        rows, total = _page_rows(
            connection, query, "SELECT COUNT(*) FROM imports", limit, offset
        )
        return _normalize_timestamps(rows, "imported_at"), total
    finally:
        connection.close()


def _position_values(position: PositionRecord, import_id: int) -> tuple[object, ...]:
    """Convert a position record to SQLite parameters."""
    return (
        position.position_id,
        position.strategy,
        int(position.is_associated),
        position.symbol_family,
        position.symbol_raw,
        position.direction,
        position.entry_at.isoformat(),
        position.exit_at.isoformat() if position.exit_at else None,
        position.status,
        position.volume_requested,
        position.volume_executed,
        position.entry_price,
        position.exit_price,
        position.commission,
        position.swap,
        position.pnl,
        import_id,
    )


def _require_import_id(value: object) -> int:
    """Return a valid SQLite import identifier or fail loudly."""
    if not isinstance(value, int):
        raise TypeError(f"identificador de importação inválido: {value!r}")
    return value


def _order_values(order: OrderRecord, import_id: int) -> tuple[object, ...]:
    """Convert an order record to SQLite parameters."""
    return (
        order.order_id,
        order.position_id,
        order.strategy,
        order.symbol_raw,
        order.direction,
        order.opened_at.isoformat(),
        order.event_at.isoformat() if order.event_at else None,
        order.status,
        order.volume_requested,
        order.volume_executed,
        order.price,
        order.stop_loss,
        order.take_profit,
        order.comment,
        import_id,
    )


def _transaction_values(
    transaction: TransactionRecord, import_id: int
) -> tuple[object, ...]:
    """Convert a transaction record to SQLite parameters."""
    return (
        transaction.transaction_id,
        transaction.order_id,
        transaction.position_id,
        transaction.strategy,
        transaction.at.isoformat(),
        transaction.symbol_raw,
        transaction.direction,
        transaction.volume,
        transaction.price,
        transaction.commission,
        transaction.tax,
        transaction.swap,
        transaction.pnl,
        transaction.balance,
        transaction.comment,
        import_id,
    )


def build_projection(
    path: Path,
    source_name: str,
    source_hash: str,
    projection: ProjectionData,
    prior_imports: Iterable[ImportHistoryRow] = (),
) -> None:
    """Build a complete SQLite projection atomically in the target file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        positions = list(projection.positions)
        orders = list(projection.orders)
        transactions = list(projection.transactions)
        rejected = list(projection.rejected)
        connection.executemany("INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)", prior_imports)
        imported_at = datetime.now(timezone.utc).isoformat()
        if existing := connection.execute(
            "SELECT id FROM imports WHERE source_hash = ?", (source_hash,)
        ).fetchone():
            import_id = _require_import_id(existing[0])
        else:
            cursor = connection.execute(
                "INSERT INTO imports("
                "source_name, source_hash, imported_at, rows_read, positions_created, "
                "no_comment_count, rejected_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    source_name,
                    source_hash,
                    imported_at,
                    projection.rows_read,
                    len(positions),
                    sum(item.strategy is None for item in positions),
                    len(rejected),
                ),
            )
            import_id = _require_import_id(cursor.lastrowid)
        connection.executemany(
            "INSERT INTO positions("
            "position_id, strategy, is_associated, symbol_family, symbol_raw, direction, entry_at, "
            "exit_at, status, volume_requested, volume_executed, entry_price, "
            "exit_price, commission, swap, pnl, import_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_position_values(position, import_id) for position in positions],
        )
        connection.executemany(
            "INSERT INTO orders("
            "order_id, position_id, strategy, symbol_raw, direction, opened_at, "
            "event_at, status, volume_requested, volume_executed, price, stop_loss, "
            "take_profit, comment, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?)",
            [_order_values(order, import_id) for order in orders],
        )
        connection.executemany(
            "INSERT INTO transactions("
            "transaction_id, order_id, position_id, strategy, at, symbol_raw, direction, "
            "volume, price, commission, tax, swap, pnl, balance, comment, import_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_transaction_values(transaction, import_id) for transaction in transactions],
        )
        connection.executemany(
            "INSERT INTO rejected_rows(import_id, row_number, position_id, reason) "
            "VALUES (?, ?, ?, ?)",
            [
                (import_id, item.row_number, item.raw_position_id, item.reason)
                for item in rejected
            ],
        )
        connection.commit()
    finally:
        connection.close()
