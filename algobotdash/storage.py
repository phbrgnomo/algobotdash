from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .parser import OrderRecord, PositionRecord, RejectedRecord, TransactionRecord

CURRENT_SCHEMA_VERSION = 2

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


def read_import_history(path: Path) -> list[tuple]:
    if not path.exists():
        return []
    connection = sqlite3.connect(path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "imports" not in tables:
            raise ValueError(f"schema SQLite incompatível: tabela imports ausente em {path}")
        version_row = connection.execute("SELECT version FROM schema_version").fetchone() if "schema_version" in tables else None
        version = version_row[0] if version_row else None
        columns = {row[1] for row in connection.execute("PRAGMA table_info(imports)")}
        if version not in (None, CURRENT_SCHEMA_VERSION):
            raise ValueError(f"versão de schema SQLite não suportada: {version}")
        if "positions_created" in columns:
            return connection.execute("SELECT id, source_name, source_hash, imported_at, rows_read, positions_created, no_comment_count, rejected_count FROM imports ORDER BY id").fetchall()
        raise ValueError(f"schema SQLite incompatível em {path}: coluna positions_created ausente")
    finally:
        connection.close()


def build_projection(
    path: Path,
    source_name: str,
    source_hash: str,
    positions: Iterable[PositionRecord],
    orders: Iterable[OrderRecord],
    transactions: Iterable[TransactionRecord],
    rejected: Iterable[RejectedRecord],
    rows_read: int,
    prior_imports: Iterable[tuple] = (),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        positions = list(positions)
        orders = list(orders)
        transactions = list(transactions)
        rejected = list(rejected)
        connection.executemany("INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)", prior_imports)
        imported_at = datetime.now(timezone.utc).isoformat()
        existing = connection.execute("SELECT id FROM imports WHERE source_hash = ?", (source_hash,)).fetchone()
        if existing:
            import_id = existing[0]
        else:
            cursor = connection.execute(
                "INSERT INTO imports(source_name, source_hash, imported_at, rows_read, positions_created, no_comment_count, rejected_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source_name, source_hash, imported_at, rows_read, len(positions), sum(item.strategy is None for item in positions), len(rejected)),
            )
            import_id = cursor.lastrowid
        connection.executemany(
            "INSERT INTO positions(position_id, strategy, symbol_family, symbol_raw, direction, entry_at, exit_at, status, volume_requested, volume_executed, entry_price, exit_price, commission, swap, pnl, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(p.position_id, p.strategy, p.symbol_family, p.symbol_raw, p.direction, p.entry_at.isoformat(), p.exit_at.isoformat() if p.exit_at else None, p.status, p.volume_requested, p.volume_executed, p.entry_price, p.exit_price, p.commission, p.swap, p.pnl, import_id) for p in positions],
        )
        connection.executemany(
            "INSERT INTO orders(order_id, position_id, strategy, symbol_raw, direction, opened_at, event_at, status, volume_requested, volume_executed, price, stop_loss, take_profit, comment, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(o.order_id, o.position_id, o.strategy, o.symbol_raw, o.direction, o.opened_at.isoformat(), o.event_at.isoformat() if o.event_at else None, o.status, o.volume_requested, o.volume_executed, o.price, o.stop_loss, o.take_profit, o.comment, import_id) for o in orders],
        )
        connection.executemany(
            "INSERT INTO transactions(transaction_id, order_id, position_id, strategy, at, symbol_raw, direction, volume, price, commission, tax, swap, pnl, balance, comment, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(t.transaction_id, t.order_id, t.position_id, t.strategy, t.at.isoformat(), t.symbol_raw, t.direction, t.volume, t.price, t.commission, t.tax, t.swap, t.pnl, t.balance, t.comment, import_id) for t in transactions],
        )
        connection.executemany(
            "INSERT INTO rejected_rows(import_id, row_number, position_id, reason) VALUES (?, ?, ?, ?)",
            [(import_id, item.row_number, item.raw_position_id, item.reason) for item in rejected],
        )
        connection.commit()
    finally:
        connection.close()
