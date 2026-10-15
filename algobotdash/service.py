"""Coordinate safe imports from workbooks into SQLite projections."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from .config import ImportConfig
from .parser import read_report
from .refresh import DatabaseRefreshLock
from .storage import ProjectionData, build_projection, read_import_history


@dataclass(frozen=True)
class ImportSummary:
    """Summary of a completed workbook import."""

    source_hash: str
    rows_read: int
    positions_created: int
    no_comment_count: int
    rejected_count: int
    database_path: Path


# pylint: disable=too-few-public-methods
class ImportService:
    """Run serialized, atomic imports for one configuration."""

    def __init__(self, config: ImportConfig):
        """Initialize the service with validated import configuration."""
        self.config = config

    def refresh(
        self,
        database_path: str | Path,
        *,
        progress: Callable[[str], None] | None = None,
        prepared: Callable[[ImportSummary], None] | None = None,
        revision: str | None = None,
    ) -> ImportSummary:
        """Refresh the database projection from the configured workbook."""
        database = Path(database_path)
        with DatabaseRefreshLock(database):
            return self.refresh_with_lock(
                database, progress=progress, prepared=prepared, revision=revision
            )

    def refresh_with_lock(
        self,
        database_path: str | Path,
        *,
        progress: Callable[[str], None] | None = None,
        prepared: Callable[[ImportSummary], None] | None = None,
        revision: str | None = None,
    ) -> ImportSummary:
        """Refresh while the caller owns the database refresh lock."""
        return self._refresh(
            Path(database_path),
            progress=progress,
            prepared=prepared,
            revision=revision,
        )

    def _refresh(  # pylint: disable=too-many-locals
        self,
        database: Path,
        *,
        progress: Callable[[str], None] | None,
        prepared: Callable[[ImportSummary], None] | None,
        revision: str | None,
    ) -> ImportSummary:
        source = self.config.source_path
        if progress is not None:
            progress("copying_source")
        snapshot_fd, snapshot_name = tempfile.mkstemp(
            prefix=f".{source.stem}.", suffix=source.suffix
        )
        os.close(snapshot_fd)
        snapshot = Path(snapshot_name)
        try:
            shutil.copyfile(source, snapshot)
            source_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            if progress is not None:
                progress("reading_source")
            positions, orders, transactions, rejected, rows_read = read_report(
                snapshot, self.config
            )
        finally:
            snapshot.unlink(missing_ok=True)
        database.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{database.name}.", suffix=".tmp", dir=database.parent
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            if progress is not None:
                progress("building_projection")
            build_projection(
                temporary,
                source.name,
                source_hash,
                ProjectionData(positions, orders, transactions, rejected, rows_read),
                self.config.timezone,
                read_import_history(database),
                revision,
            )
            summary = ImportSummary(
                source_hash,
                rows_read,
                len(positions),
                sum(record.strategy is None for record in positions),
                len(rejected),
                database,
            )
            if prepared is not None:
                prepared(summary)
            if progress is not None:
                progress("publishing")
            os.replace(temporary, database)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return summary
