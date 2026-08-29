from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import os
import tempfile

from .config import ImportConfig
from .parser import read_positions
from .storage import build_projection


@dataclass(frozen=True)
class ImportSummary:
    source_hash: str
    rows_read: int
    cycles_created: int
    no_comment_count: int
    rejected_count: int
    database_path: Path


class ImportService:
    def __init__(self, config: ImportConfig):
        self.config = config

    def refresh(self, database_path: str | Path) -> ImportSummary:
        source = self.config.source_path
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        records, rejected = read_positions(source, self.config)
        rows_read = len(records) + len(rejected)
        database = Path(database_path)
        database.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{database.name}.", suffix=".tmp", dir=database.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            build_projection(temporary, source.name, source_hash, records, rejected, rows_read)
            os.replace(temporary, database)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return ImportSummary(source_hash, rows_read, len(records), sum(r.strategy is None for r in records), len(rejected), database)
