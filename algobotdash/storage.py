"""Persist import projections and their history in SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeAlias

from .parser import OrderRecord, PositionRecord, RejectedRecord, TransactionRecord

CURRENT_SCHEMA_VERSION = 2
ImportHistoryRow: TypeAlias = tuple[int, str, str, str, int, int, int, int]
ProjectionRow: TypeAlias = dict[str, Any]

POSITION_SORT_COLUMNS = {
    "opened_at": "utc_timestamp(entry_at)",
    "closed_at": "utc_timestamp(exit_at)",
    "realized_pnl": "pnl",
    "symbol_family": "symbol_family",
    "status": "status",
}

REQUIRED_TABLE_COLUMNS = {
    "schema_version": {"version"},
    "imports": {
        "id", "source_name", "source_hash", "imported_at", "rows_read",
        "positions_created", "no_comment_count", "rejected_count",
    },
    "positions": {
        "id", "position_id", "strategy", "symbol_family", "symbol_raw", "direction",
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
class ProjectionData:
    """Parsed records and counters required to build a projection."""

    positions: Iterable[PositionRecord]
    orders: Iterable[OrderRecord]
    transactions: Iterable[TransactionRecord]
    rejected: Iterable[RejectedRecord]
    rows_read: int

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE schema_version (
  version INTEGER PRIMARY KEY
);
INSERT INTO schema_version(version) VALUES (2);
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
        if "schema_version" not in tables:
            raise ValueError(f"schema SQLite incompatível em {path}: tabela schema_version ausente")
        version_row = connection.execute("SELECT version FROM schema_version").fetchone()
        version = version_row[0] if version_row else None
        columns = {row[1] for row in connection.execute("PRAGMA table_info(imports)")}
        if version != CURRENT_SCHEMA_VERSION:
            raise ValueError(f"versão de schema SQLite não suportada: {version}")
        if "positions_created" in columns:
            return connection.execute(
                "SELECT id, source_name, source_hash, imported_at, rows_read, "
                "positions_created, no_comment_count, rejected_count "
                "FROM imports ORDER BY id"
            ).fetchall()
        raise ValueError(f"schema SQLite incompatível em {path}: coluna positions_created ausente")
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
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if missing_columns := required_columns - columns:
                raise ValueError(
                    f"colunas ausentes em {table}: {sorted(missing_columns)}"
                )
        version_row = connection.execute("SELECT version FROM schema_version").fetchone()
        version = version_row[0] if version_row else None
        if version != CURRENT_SCHEMA_VERSION:
            raise ValueError(f"versão de schema SQLite não suportada: {version}")
        return connection
    except (OSError, sqlite3.Error, ValueError) as exc:
        if connection is not None:
            connection.close()
        raise ProjectionUnavailableError("projeção SQLite indisponível") from exc


def _page_rows(
    connection: sqlite3.Connection,
    query: str,
    count_query: str,
    limit: int,
    offset: int,
) -> tuple[list[ProjectionRow], int]:
    """Return a page and its total from a read-only projection query."""
    try:
        total = connection.execute(count_query).fetchone()[0]
        rows = connection.execute(query, (limit, offset)).fetchall()
    except sqlite3.Error as exc:
        raise ProjectionUnavailableError("projeção SQLite indisponível") from exc
    return [dict(row) for row in rows], total


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


def _normalize_timestamps(
    rows: list[ProjectionRow], *fields: str
) -> list[ProjectionRow]:
    """Normalize selected timestamp fields while keeping other row values intact."""
    for row in rows:
        for field in fields:
            row[field] = _utc_timestamp(row[field])
    return rows


def read_positions(
    path: Path,
    *,
    limit: int,
    offset: int,
    sort_by: str,
    sort_order: str,
) -> tuple[list[ProjectionRow], int]:
    """Read a deterministic page of analytical positions from the projection."""
    column = POSITION_SORT_COLUMNS[sort_by]
    order = sort_order.upper()
    query = (
        "SELECT position_id, strategy, symbol_family, "
        "CASE WHEN strategy IS NOT NULL AND symbol_family IS NOT NULL "
        "THEN symbol_family || ' ' || strategy END AS strategy_key, "
        "direction, entry_at AS opened_at, "
        "exit_at AS closed_at, status, "
        "CASE WHEN status = 'open' THEN NULL ELSE pnl END AS realized_pnl "
        "FROM positions "
        f"ORDER BY {column} {order}, position_id ASC LIMIT ? OFFSET ?"
    )
    connection = _open_projection(path)
    try:
        rows, total = _page_rows(
            connection,
            query,
            "SELECT COUNT(*) FROM positions",
            limit,
            offset,
        )
        return _normalize_timestamps(rows, "opened_at", "closed_at"), total
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
        "ORDER BY imported_at DESC, id DESC LIMIT ? OFFSET ?"
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
            "position_id, strategy, symbol_family, symbol_raw, direction, entry_at, "
            "exit_at, status, volume_requested, volume_executed, entry_price, "
            "exit_price, commission, swap, pnl, import_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
