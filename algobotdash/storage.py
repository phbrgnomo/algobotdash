"""Persist import projections and their history in SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeAlias

from .parser import OrderRecord, PositionRecord, RejectedRecord, TransactionRecord

CURRENT_SCHEMA_VERSION = 2
ImportHistoryRow: TypeAlias = tuple[int, str, str, str, int, int, int, int]


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
        existing = connection.execute(
            "SELECT id FROM imports WHERE source_hash = ?", (source_hash,)
        ).fetchone()
        if existing:
            import_id = existing[0]
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
            import_id = cursor.lastrowid
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
