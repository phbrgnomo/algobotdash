"""Run observable projection refreshes outside the HTTP request lifecycle."""

from __future__ import annotations

import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import ConfigurationError, load_config
from .refresh import (
    DatabaseRefreshLock,
    RefreshInProgressError,
    RefreshOperation,
    RefreshOperationStore,
)
from .service import ImportService, ImportSummary

logger = logging.getLogger(__name__)

_executors: dict[Path, ThreadPoolExecutor] = {}
_executors_guard = threading.Lock()
_live_operations: set[str] = set()
_live_guard = threading.Lock()


def _live_snapshot() -> set[str]:
    with _live_guard:
        return set(_live_operations)


def _executor_for_database(database: Path) -> ThreadPoolExecutor:
    """Serialize one projection without blocking refreshes of unrelated databases."""
    key = database.resolve()
    with _executors_guard:
        executor = _executors.get(key)
        if executor is None:
            executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=f"algobotdash-refresh-{len(_executors) + 1}",
            )
            _executors[key] = executor
        return executor


def _summary_payload(summary: ImportSummary) -> dict[str, object]:
    return {
        "source_hash": summary.source_hash,
        "rows_read": summary.rows_read,
        "positions_created": summary.positions_created,
        "no_comment_count": summary.no_comment_count,
        "rejected_count": summary.rejected_count,
    }


def _public_error(exc: Exception) -> tuple[str, str]:
    message = str(exc).splitlines()[0][:500] or exc.__class__.__name__
    if isinstance(exc, ConfigurationError):
        return "configuration_error", message
    if isinstance(exc, FileNotFoundError):
        return "source_unavailable", message
    if isinstance(exc, PermissionError):
        return "filesystem_error", message
    if isinstance(exc, ValueError):
        return "validation_error", message
    return "refresh_failed", "A atualização falhou. Consulte os logs do serviço."


class RefreshRunner:
    """Coordinate admission, persistence and background import execution."""

    def __init__(self, config_path: str | Path, database_path: str | Path):
        self.config_path = Path(config_path)
        self.database_path = Path(database_path)
        self.store = RefreshOperationStore.for_database(self.database_path)
        self._reconcile_if_idle()

    def _reconcile_if_idle(self) -> None:
        """Recover stale operations only when no importer owns the projection."""
        if not self.store.has_unresolved(_live_snapshot()):
            return
        lock = DatabaseRefreshLock(self.database_path)
        try:
            lock.acquire()
        except RefreshInProgressError:
            return
        try:
            self.store.reconcile_interrupted(self.database_path, _live_snapshot())
        finally:
            lock.release()

    def start(self) -> RefreshOperation:
        """Admit one refresh and return after durable scheduling."""
        lock = DatabaseRefreshLock(self.database_path)
        lock.acquire()
        try:
            self.store.reconcile_interrupted(self.database_path, _live_snapshot())
            operation = self.store.create()
            with _live_guard:
                _live_operations.add(operation.operation_id)
            try:
                _ = _executor_for_database(self.database_path).submit(
                    self._run, operation, lock
                )
            except Exception:
                with _live_guard:
                    _live_operations.discard(operation.operation_id)
                self.store.fail(
                    operation.operation_id,
                    "scheduling_failed",
                    "Não foi possível agendar a atualização.",
                )
                raise
            return operation
        except Exception:
            lock.release()
            raise

    def get(self, operation_id: str) -> RefreshOperation | None:
        """Read one persisted attempt."""
        return self.store.get(operation_id)

    def latest(self) -> RefreshOperation | None:
        """Read the latest HTTP refresh attempt."""
        return self.store.latest()

    def _run(self, operation: RefreshOperation, lock: DatabaseRefreshLock) -> None:
        operation_id = operation.operation_id
        published = False
        summary: ImportSummary | None = None
        try:
            try:
                self.store.mark_running(operation_id, "validating_configuration")
                config = load_config(self.config_path)
                summary = ImportService(config).refresh_with_lock(
                    self.database_path,
                    progress=lambda stage: self.store.update_stage(operation_id, stage),
                    prepared=lambda result: self.store.prepare_summary(
                        operation_id, _summary_payload(result)
                    ),
                    revision=operation.expected_revision,
                )
                published = True
            # The worker boundary turns import failures into a terminal state.
            except Exception as exc:  # pylint: disable=broad-exception-caught
                code, message = _public_error(exc)
                try:
                    self.store.fail(operation_id, code, message)
                except sqlite3.Error:
                    logger.exception("não foi possível persistir a falha da atualização")
                logger.warning(
                    "atualização %s falhou: %s",
                    operation_id,
                    exc,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )
            if published and summary is not None:
                try:
                    self.store.complete(operation_id, _summary_payload(summary))
                except sqlite3.Error:
                    logger.exception(
                        "a projeção foi publicada, mas o estado final será reconciliado"
                    )
        finally:
            with _live_guard:
                _live_operations.discard(operation_id)
            lock.release()


__all__ = ["RefreshInProgressError", "RefreshRunner"]
