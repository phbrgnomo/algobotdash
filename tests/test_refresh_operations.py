"""Behavioral tests for persistent refresh operations and import exclusion."""

from __future__ import annotations

import sqlite3
import subprocess  # nosec B404 -- fixed interpreter and test helper code
import sys
import tempfile
import unittest
import shutil
from pathlib import Path

from algobotdash.refresh import (
    DatabaseRefreshLock,
    RefreshInProgressError,
    RefreshOperationStore,
)


class RefreshOperationTests(unittest.TestCase):
    """Verify refresh state through its persistent public interface."""

    workspace: Path = Path()
    database: Path = Path()
    refresh_store: RefreshOperationStore | None = None

    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="algobotdash-refresh-"))
        self.database = self.workspace / "algobotdash.sqlite"
        self.refresh_store = RefreshOperationStore.for_database(self.database)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace)

    @property
    def store(self) -> RefreshOperationStore:
        """Return the store initialized for the current test workspace."""
        if self.refresh_store is None:
            raise RuntimeError("refresh store was not initialized")
        return self.refresh_store

    def test_operation_survives_store_recreation(self) -> None:
        """A completed attempt remains queryable after the process-local store is gone."""
        operation = self.store.create("revision-1")
        self.store.mark_running(operation.operation_id, "reading_source")
        self.store.complete(
            operation.operation_id,
            {
                "source_hash": "abc",
                "rows_read": 12,
                "positions_created": 3,
                "no_comment_count": 1,
                "rejected_count": 0,
            },
        )

        restored = RefreshOperationStore.for_database(self.database).get(
            operation.operation_id
        )

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.state, "completed")
        self.assertEqual(restored.summary["positions_created"], 3)
        self.assertIsNotNone(restored.started_at)
        self.assertIsNotNone(restored.finished_at)

    def test_interrupted_operation_reconciles_with_published_revision(self) -> None:
        """Recovery reports success when the operation's exact revision was published."""
        operation = self.store.create("published-revision")
        self.store.mark_running(operation.operation_id, "publishing")
        self.store.prepare_summary(
            operation.operation_id,
            {"source_hash": "published-hash", "positions_created": 4},
        )
        connection = sqlite3.connect(self.database)
        _ = connection.execute(
            "CREATE TABLE projection_metadata "
            "(id INTEGER PRIMARY KEY, revision TEXT, timezone TEXT)"
        )
        _ = connection.execute(
            "INSERT INTO projection_metadata VALUES (1, 'published-revision', 'America/Bahia')"
        )
        connection.commit()
        connection.close()

        self.store.reconcile_interrupted(self.database, live_operation_ids=set())

        restored = self.store.get(operation.operation_id)
        assert restored is not None
        self.assertEqual(restored.state, "completed")
        self.assertEqual(restored.stage, "completed_after_restart")
        self.assertEqual(restored.summary["source_hash"], "published-hash")
        self.assertEqual(restored.summary["positions_created"], 4)

    def test_interrupted_operation_without_publication_becomes_error(self) -> None:
        """Recovery exposes an interruption when the expected revision is absent."""
        operation = self.store.create("missing-revision")
        self.store.mark_running(operation.operation_id, "building_projection")

        self.store.reconcile_interrupted(self.database, live_operation_ids=set())

        restored = self.store.get(operation.operation_id)
        assert restored is not None
        self.assertEqual(restored.state, "error")
        self.assertEqual(restored.error_code, "operation_interrupted")

    def test_database_lock_rejects_a_second_importer(self) -> None:
        """Only one importer can own a projection, even through separate lock objects."""
        first = DatabaseRefreshLock(self.database)
        second = DatabaseRefreshLock(self.database)
        first.acquire()
        try:
            with self.assertRaises(RefreshInProgressError):
                second.acquire()
        finally:
            first.release()

        second.acquire()
        second.release()

    def test_database_lock_rejects_another_process(self) -> None:
        """The file lock coordinates the web process with a separate CLI process."""
        helper = (
            "from algobotdash.refresh import DatabaseRefreshLock; import sys; "
            "lock=DatabaseRefreshLock(sys.argv[1]); lock.acquire(); "
            "print('ready', flush=True); sys.stdin.readline(); lock.release()"
        )
        with subprocess.Popen(  # nosec B603 -- fixed interpreter and code
            [sys.executable, "-c", helper, str(self.database)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            try:
                assert process.stdout is not None
                self.assertEqual(process.stdout.readline().strip(), "ready")
                with self.assertRaises(RefreshInProgressError):
                    DatabaseRefreshLock(self.database).acquire()
            finally:
                if process.stdin is not None:
                    _ = process.stdin.write("release\n")
                    process.stdin.flush()
                _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr)


if __name__ == "__main__":
    unittest.main()
