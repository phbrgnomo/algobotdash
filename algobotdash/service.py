from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import ImportConfig
from .parser import read_report
from .storage import build_projection, read_import_history


@dataclass(frozen=True)
class ImportSummary:
    source_hash: str
    rows_read: int
    positions_created: int
    no_comment_count: int
    rejected_count: int
    database_path: Path

class ImportService:
    def __init__(self, config: ImportConfig):
        self.config = config

    def refresh(self, database_path: str | Path) -> ImportSummary:
        source = self.config.source_path
        snapshot_fd, snapshot_name = tempfile.mkstemp(prefix=f".{source.stem}.", suffix=source.suffix)
        os.close(snapshot_fd)
        snapshot = Path(snapshot_name)
        try:
            shutil.copyfile(source, snapshot)
            source_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            positions, orders, transactions, rejected, rows_read = read_report(snapshot, self.config)
        finally:
            snapshot.unlink(missing_ok=True)
        database = Path(database_path)
        database.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{database.name}.", suffix=".tmp", dir=database.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            build_projection(temporary, source.name, source_hash, positions, orders, transactions, rejected, rows_read, read_import_history(database))
            os.replace(temporary, database)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return ImportSummary(source_hash, rows_read, len(positions), sum(r.strategy is None for r in positions), len(rejected), database)
