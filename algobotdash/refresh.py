"""Persistent refresh attempts and cross-process import exclusion."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TextIO

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised by patch on the Linux test runtime
    _fcntl = None

RefreshState = Literal["queued", "running", "completed", "error"]


class RefreshInProgressError(RuntimeError):
    """Raised when another process or thread is already refreshing a projection."""


class RefreshLockUnavailableError(RuntimeError):
    """Raised when the runtime cannot provide the required process lock."""


@dataclass(frozen=True)
# pylint: disable=too-many-instance-attributes
class RefreshOperation:
    """One persisted attempt to rebuild the derived projection."""

    operation_id: str
    state: RefreshState
    stage: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    expected_revision: str
    summary: dict[str, Any]
    error_code: str | None
    error_message: str | None

    def as_dict(self) -> dict[str, Any]:
        """Return the stable HTTP representation."""
        return {
            "operation_id": self.operation_id,
            "state": self.state,
            "stage": self.stage,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.summary or None,
            "error": (
                {"code": self.error_code, "message": self.error_message}
                if self.error_code is not None
                else None
            ),
        }


OPERATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS refresh_operations (
  operation_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'completed', 'error')),
  stage TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  expected_revision TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}',
  error_code TEXT,
  error_message TEXT
);
CREATE INDEX IF NOT EXISTS refresh_operations_created
ON refresh_operations(created_at DESC, operation_id DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RefreshOperationStore:
    """Store refresh attempts independently from the replaceable projection."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(OPERATIONS_SCHEMA)

    @classmethod
    def for_database(cls, database: str | Path) -> "RefreshOperationStore":
        """Place operation state beside its projection."""
        database_path = Path(database)
        return cls(database_path.with_name(f"{database_path.name}.operations.sqlite"))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self, expected_revision: str | None = None) -> RefreshOperation:
        """Persist a queued attempt before scheduling background work."""
        operation_id = uuid.uuid4().hex
        revision = expected_revision or uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO refresh_operations("
                "operation_id, state, stage, created_at, expected_revision) "
                "VALUES (?, 'queued', 'queued', ?, ?)",
                (operation_id, _now(), revision),
            )
        operation = self.get(operation_id)
        if operation is None:  # pragma: no cover - SQLite acknowledged the insert
            raise RuntimeError("não foi possível registrar a atualização")
        return operation

    def mark_running(self, operation_id: str, stage: str) -> None:
        """Start an attempt and publish its current stage."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE refresh_operations SET state = 'running', stage = ?, "
                "started_at = COALESCE(started_at, ?) WHERE operation_id = ?",
                (stage, _now(), operation_id),
            )

    def update_stage(self, operation_id: str, stage: str) -> None:
        """Publish a real import stage without inventing percentage progress."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE refresh_operations SET stage = ? "
                "WHERE operation_id = ? AND state = 'running'",
                (stage, operation_id),
            )

    def prepare_summary(self, operation_id: str, summary: dict[str, Any]) -> None:
        """Persist the result before its corresponding projection is published."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE refresh_operations SET summary_json = ? "
                "WHERE operation_id = ? AND state = 'running'",
                (json.dumps(summary, ensure_ascii=False), operation_id),
            )

    def complete(
        self,
        operation_id: str,
        summary: dict[str, Any] | None = None,
        *,
        stage: str = "completed",
    ) -> None:
        """Finish an attempt successfully."""
        assignments = (
            "state = 'completed', stage = ?, finished_at = ?, "
            "error_code = NULL, error_message = NULL"
        )
        parameters: tuple[object, ...] = (stage, _now())
        if summary is not None:
            assignments += ", summary_json = ?"
            parameters += (json.dumps(summary, ensure_ascii=False),)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE refresh_operations SET {assignments} WHERE operation_id = ?",
                (*parameters, operation_id),
            )

    def fail(self, operation_id: str, code: str, message: str) -> None:
        """Finish an attempt with a controlled public error."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE refresh_operations SET state = 'error', stage = 'error', "
                "finished_at = ?, error_code = ?, error_message = ? WHERE operation_id = ?",
                (_now(), code, message[:500], operation_id),
            )

    def get(self, operation_id: str) -> RefreshOperation | None:
        """Read one attempt by its stable identifier."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM refresh_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._operation(row) if row is not None else None

    def latest(self) -> RefreshOperation | None:
        """Return the last persisted HTTP refresh attempt."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM refresh_operations "
                "ORDER BY created_at DESC, operation_id DESC LIMIT 1"
            ).fetchone()
        return self._operation(row) if row is not None else None

    def reconcile_interrupted(
        self, database: str | Path, live_operation_ids: set[str]
    ) -> None:
        """Resolve attempts left active by a prior process."""
        revision = _published_revision(Path(database))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT operation_id, expected_revision FROM refresh_operations "
                "WHERE state IN ('queued', 'running')"
            ).fetchall()
        for row in rows:
            operation_id = str(row["operation_id"])
            if operation_id in live_operation_ids:
                continue
            if revision == row["expected_revision"]:
                self.complete(operation_id, stage="completed_after_restart")
            else:
                self.fail(
                    operation_id,
                    "operation_interrupted",
                    "A atualização foi interrompida antes da publicação.",
                )

    @staticmethod
    def _operation(row: sqlite3.Row) -> RefreshOperation:
        summary = json.loads(row["summary_json"])
        return RefreshOperation(
            operation_id=str(row["operation_id"]),
            state=row["state"],
            stage=str(row["stage"]),
            created_at=str(row["created_at"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            expected_revision=str(row["expected_revision"]),
            summary=summary,
            error_code=row["error_code"],
            error_message=row["error_message"],
        )


def _published_revision(database: Path) -> str | None:
    if not database.is_file():
        return None
    try:
        database_uri = f"{database.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(database_uri, uri=True)
        try:
            row = connection.execute(
                "SELECT revision FROM projection_metadata WHERE id = 1"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return str(row[0]) if row is not None else None


_thread_locks: dict[Path, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _thread_lock(path: Path) -> threading.Lock:
    key = path.resolve()
    with _thread_locks_guard:
        return _thread_locks.setdefault(key, threading.Lock())


class DatabaseRefreshLock:
    """Hold one non-blocking import lock across threads and Linux processes."""

    def __init__(self, database: str | Path):
        self.database = Path(database).resolve()
        self.path = self.database.with_name(f"{self.database.name}.refresh.lock")
        self._thread_lock = _thread_lock(self.database)
        self._stream: TextIO | None = None

    def acquire(self) -> None:
        """Acquire immediately or report that another importer owns the database."""
        if _fcntl is None:
            raise RefreshLockUnavailableError(
                "o bloqueio entre processos exige um runtime POSIX com fcntl"
            )
        if not self._thread_lock.acquire(blocking=False):
            raise RefreshInProgressError("já existe uma atualização em andamento")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stream = self.path.open("a+", encoding="utf-8")  # pylint: disable=consider-using-with
            try:
                _fcntl.flock(stream.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except OSError as exc:
                stream.close()
                if isinstance(exc, BlockingIOError):
                    raise RefreshInProgressError(
                        "já existe uma atualização em andamento"
                    ) from exc
                raise
            self._stream = stream
        except Exception:
            self._thread_lock.release()
            raise

    def release(self) -> None:
        """Release the process and thread lock."""
        if self._stream is None:
            return
        try:
            try:
                if _fcntl is not None:
                    _fcntl.flock(self._stream.fileno(), _fcntl.LOCK_UN)
            finally:
                self._stream.close()
        finally:
            self._stream = None
            self._thread_lock.release()

    def __enter__(self) -> "DatabaseRefreshLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
