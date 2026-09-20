"""Behavioral tests for persistent refresh operations and import exclusion."""

from __future__ import annotations

import sqlite3
import subprocess  # nosec B404 -- fixed interpreter and test helper code
import sys
import tempfile
import threading
import time
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch

from algobotdash.refresh import (
    DatabaseRefreshLock,
    RefreshInProgressError,
    RefreshLockUnavailableError,
    RefreshOperationStore,
)
from algobotdash.refresh_runner import RefreshRunner
from algobotdash.service import ImportService, ImportSummary
from tests.fixture_helpers import workbook


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

    def _write_config(self, source: Path) -> Path:
        config_path = self.workspace / f"{source.stem}.yaml"
        _ = config_path.write_text(
            f"timezone: America/Bahia\nsource:\n  path: {source}\n",
            encoding="utf-8",
        )
        return config_path

    def _wait_for_operation(
        self,
        store: RefreshOperationStore,
        operation_id: str,
    ):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            operation = store.get(operation_id)
            if operation is not None and operation.state in {"completed", "error"}:
                return operation
            time.sleep(0.01)
        self.fail("refresh operation did not finish")

    def _assert_lock_released(self, database: Path) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            lock = DatabaseRefreshLock(database)
            try:
                lock.acquire()
            except RefreshInProgressError:
                time.sleep(0.01)
                continue
            lock.release()
            return
        self.fail("refresh lock was not released")

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

    def test_runner_construction_does_not_lock_without_stale_operations(self) -> None:
        """Routine polling cannot briefly block admission for an idle projection."""
        config_path = self.workspace / "config.yaml"

        with patch.object(
            DatabaseRefreshLock,
            "acquire",
            autospec=True,
            side_effect=AssertionError("idle reconciliation acquired admission lock"),
        ) as acquire:
            runner = RefreshRunner(config_path, self.database)

        acquire.assert_not_called()
        self.assertIsNone(runner.latest())

    def test_live_operation_does_not_trigger_constructor_reconciliation(self) -> None:
        """Polling ignores an operation that belongs to this process's worker."""
        source = self.workspace / "ReportHistory.xlsx"
        workbook(source)
        config_path = self._write_config(source)
        runner = RefreshRunner(config_path, self.database)
        worker_started = threading.Event()
        release_worker = threading.Event()

        def blocked_refresh(
            _service: ImportService,
            database_path: str | Path,
            **_kwargs: object,
        ) -> ImportSummary:
            worker_started.set()
            if not release_worker.wait(timeout=5):
                raise TimeoutError("worker was not released")
            return ImportSummary("hash", 1, 1, 0, 0, Path(database_path))

        with patch.object(
            ImportService,
            "refresh_with_lock",
            autospec=True,
            side_effect=blocked_refresh,
        ):
            operation = runner.start()
            self.assertTrue(worker_started.wait(timeout=5))
            with patch.object(
                DatabaseRefreshLock,
                "acquire",
                autospec=True,
                side_effect=AssertionError("live operation triggered reconciliation"),
            ) as acquire:
                polled = RefreshRunner(config_path, self.database).get(
                    operation.operation_id
                )
            acquire.assert_not_called()
            release_worker.set()
            completed = self._wait_for_operation(runner.store, operation.operation_id)

        self.assertIsNotNone(polled)
        self.assertEqual(completed.state, "completed")

    def test_reconciliation_handles_sqlite_uri_characters_in_database_name(self) -> None:
        """Read-only recovery opens the exact projection path after URI quoting."""
        database = self.workspace / "projection?#%.sqlite"
        store = RefreshOperationStore.for_database(database)
        operation = store.create("special-path-revision")
        store.mark_running(operation.operation_id, "publishing")
        connection = sqlite3.connect(database)
        _ = connection.execute(
            "CREATE TABLE projection_metadata "
            "(id INTEGER PRIMARY KEY, revision TEXT, timezone TEXT)"
        )
        _ = connection.execute(
            "INSERT INTO projection_metadata VALUES "
            "(1, 'special-path-revision', 'America/Bahia')"
        )
        connection.commit()
        connection.close()

        store.reconcile_interrupted(database, live_operation_ids=set())

        restored = store.get(operation.operation_id)
        assert restored is not None
        self.assertEqual(restored.state, "completed")

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

    def test_lock_backend_failure_is_clear_and_does_not_poison_thread_lock(self) -> None:
        """Importing remains portable and unsupported locking fails only when used."""
        lock = DatabaseRefreshLock(self.database)
        with patch("algobotdash.refresh._fcntl", None):
            with self.assertRaisesRegex(RefreshLockUnavailableError, "POSIX"):
                lock.acquire()

        lock.acquire()
        lock.release()

    def test_completed_operation_is_available_from_a_new_runner(self) -> None:
        """A new runner reads the durable state and summary left by its predecessor."""
        source = self.workspace / "ReportHistory.xlsx"
        workbook(source)
        config_path = self._write_config(source)
        runner = RefreshRunner(config_path, self.database)

        operation = runner.start()
        completed = self._wait_for_operation(runner.store, operation.operation_id)
        restored = RefreshRunner(config_path, self.database).get(operation.operation_id)

        self.assertEqual(completed.state, "completed")
        assert restored is not None
        self.assertEqual(restored.state, "completed")
        self.assertEqual(restored.summary["positions_created"], 2)

    def test_runner_maps_configuration_failure_and_releases_lock(self) -> None:
        """Invalid YAML reaches a terminal code without retaining admission state."""
        config_path = self.workspace / "invalid.yaml"
        _ = config_path.write_text("timezone: [", encoding="utf-8")
        runner = RefreshRunner(config_path, self.database)

        operation = runner.start()
        failed = self._wait_for_operation(runner.store, operation.operation_id)

        self.assertEqual(failed.error_code, "configuration_error")
        self._assert_lock_released(self.database)

    def test_runner_maps_missing_source_and_releases_all_admission_state(self) -> None:
        """A missing source has a stable code and cannot leave the database blocked."""
        source = self.workspace / "missing.xlsx"
        config_path = self._write_config(source)
        runner = RefreshRunner(config_path, self.database)

        operation = runner.start()
        failed = self._wait_for_operation(runner.store, operation.operation_id)

        self.assertEqual(failed.error_code, "source_unavailable")
        self._assert_lock_released(self.database)
        restored = RefreshRunner(config_path, self.database).get(operation.operation_id)
        assert restored is not None
        self.assertEqual(restored.error_code, "source_unavailable")

    def test_runner_maps_validation_failure_and_releases_lock(self) -> None:
        """A parser validation failure becomes terminal without blocking another import."""
        source = self.workspace / "ReportHistory.xlsx"
        workbook(source)
        config_path = self._write_config(source)
        runner = RefreshRunner(config_path, self.database)

        with patch.object(
            ImportService,
            "refresh_with_lock",
            autospec=True,
            side_effect=ValueError("workbook inválido"),
        ):
            operation = runner.start()
            failed = self._wait_for_operation(runner.store, operation.operation_id)

        self.assertEqual(failed.error_code, "validation_error")
        self._assert_lock_released(self.database)

    def test_runner_cleans_up_when_failure_state_cannot_be_persisted(self) -> None:
        """A failed terminal write still releases the lock and live-operation marker."""
        source = self.workspace / "missing.xlsx"
        config_path = self._write_config(source)
        runner = RefreshRunner(config_path, self.database)
        fail_called = threading.Event()

        def fail_write(*_args: object, **_kwargs: object) -> None:
            fail_called.set()
            raise sqlite3.OperationalError("injected state failure")

        with patch.object(
            RefreshOperationStore,
            "fail",
            autospec=True,
            side_effect=fail_write,
        ):
            operation = runner.start()
            self.assertTrue(fail_called.wait(timeout=5))
            self._assert_lock_released(self.database)

        restored = RefreshRunner(config_path, self.database).get(operation.operation_id)
        assert restored is not None
        self.assertEqual(restored.error_code, "operation_interrupted")

    def test_refreshes_for_different_databases_run_independently(self) -> None:
        """A blocked projection does not occupy another projection's worker."""
        first_database = self.workspace / "first.sqlite"
        second_database = self.workspace / "second.sqlite"
        source = self.workspace / "ReportHistory.xlsx"
        workbook(source)
        config_path = self._write_config(source)
        first_runner = RefreshRunner(config_path, first_database)
        second_runner = RefreshRunner(config_path, second_database)
        first_started = threading.Event()
        release_first = threading.Event()
        second_finished = threading.Event()

        def controlled_refresh(
            _service: ImportService,
            database_path: str | Path,
            **_kwargs: object,
        ) -> ImportSummary:
            if Path(database_path) == first_database:
                first_started.set()
                if not release_first.wait(timeout=5):
                    raise TimeoutError("first database was not released")
            else:
                second_finished.set()
            return ImportSummary("hash", 1, 1, 0, 0, Path(database_path))

        with patch.object(
            ImportService,
            "refresh_with_lock",
            autospec=True,
            side_effect=controlled_refresh,
        ):
            first = first_runner.start()
            self.assertTrue(first_started.wait(timeout=5))
            second = second_runner.start()
            self.assertTrue(second_finished.wait(timeout=1))
            second_result = self._wait_for_operation(
                second_runner.store, second.operation_id
            )
            release_first.set()
            first_result = self._wait_for_operation(first_runner.store, first.operation_id)

        self.assertEqual(second_result.state, "completed")
        self.assertEqual(first_result.state, "completed")


if __name__ == "__main__":
    unittest.main()
