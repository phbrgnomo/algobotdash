from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .parser import PositionRecord, RejectedRecord


SCHEMA = """
CREATE TABLE imports (
  id INTEGER PRIMARY KEY,
  source_name TEXT NOT NULL,
  source_hash TEXT NOT NULL UNIQUE,
  imported_at TEXT NOT NULL,
  rows_read INTEGER NOT NULL,
  cycles_created INTEGER NOT NULL,
  no_comment_count INTEGER NOT NULL,
  rejected_count INTEGER NOT NULL
);
CREATE TABLE cycles (
  id INTEGER PRIMARY KEY,
  position_id TEXT NOT NULL UNIQUE,
  strategy TEXT,
  symbol_family TEXT,
  symbol_raw TEXT NOT NULL,
  direction TEXT NOT NULL,
  entry_at TEXT NOT NULL,
  exit_at TEXT,
  status TEXT NOT NULL,
  volume REAL,
  pnl REAL NOT NULL,
  import_id INTEGER NOT NULL REFERENCES imports(id)
);
CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  cycle_id INTEGER NOT NULL REFERENCES cycles(id),
  position_id TEXT NOT NULL,
  entry_at TEXT NOT NULL,
  exit_at TEXT,
  entry_price REAL,
  exit_price REAL,
  volume REAL,
  commission REAL NOT NULL,
  swap REAL NOT NULL,
  pnl REAL NOT NULL,
  comment TEXT NOT NULL
);
CREATE TABLE rejected_rows (
  id INTEGER PRIMARY KEY,
  import_id INTEGER NOT NULL REFERENCES imports(id),
  row_number INTEGER NOT NULL,
  position_id TEXT NOT NULL,
  reason TEXT NOT NULL
);
"""


def build_projection(path: Path, source_name: str, source_hash: str, records: Iterable[PositionRecord], rejected: Iterable[RejectedRecord], rows_read: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        records = list(records)
        rejected = list(rejected)
        imported_at = datetime.now(timezone.utc).isoformat()
        no_comment_count = sum(record.strategy is None for record in records)
        cursor = connection.execute(
            "INSERT INTO imports(source_name, source_hash, imported_at, rows_read, cycles_created, no_comment_count, rejected_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (source_name, source_hash, imported_at, rows_read, len(records), no_comment_count, len(rejected)),
        )
        import_id = cursor.lastrowid
        for record in records:
            cursor = connection.execute(
                "INSERT INTO cycles(position_id, strategy, symbol_family, symbol_raw, direction, entry_at, exit_at, status, volume, pnl, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record.position_id, record.strategy, record.symbol_family, record.symbol_raw, record.direction, record.entry_at.isoformat(), record.exit_at.isoformat() if record.exit_at else None, record.status, record.volume, record.pnl, import_id),
            )
            connection.execute(
                "INSERT INTO orders(cycle_id, position_id, entry_at, exit_at, entry_price, exit_price, volume, commission, swap, pnl, comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cursor.lastrowid, record.position_id, record.entry_at.isoformat(), record.exit_at.isoformat() if record.exit_at else None, record.entry_price, record.exit_price, record.volume, record.commission, record.swap, record.pnl, record.comment),
            )
        connection.executemany(
            "INSERT INTO rejected_rows(import_id, row_number, position_id, reason) VALUES (?, ?, ?, ?)",
            [(import_id, item.row_number, item.raw_position_id, item.reason) for item in rejected],
        )
        connection.commit()
    finally:
        connection.close()
