"""Docker Compose integration tests for the dashboard runtime."""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404: test harness intentionally invokes Docker CLI
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.fixture_helpers import workbook

DOCKER_CHECK_TIMEOUT = 5


def docker_compose_available() -> bool:
    """Return whether Docker Compose and its daemon respond promptly."""
    if shutil.which("docker") is None:
        return False
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        compose = subprocess.run(  # nosec B603: executable and arguments are fixed
            [docker, "compose", "version"],
            capture_output=True,
            check=False,
            timeout=DOCKER_CHECK_TIMEOUT,
        )
        daemon = subprocess.run(  # nosec B603: executable and arguments are fixed
            [docker, "info"],
            capture_output=True,
            check=False,
            timeout=DOCKER_CHECK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False
    return compose.returncode == 0 and daemon.returncode == 0


def docker_path() -> str:
    """Return the absolute Docker executable path."""
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("Docker não está disponível")
    return docker


@unittest.skipUnless(docker_compose_available(), "Docker Compose não está disponível")
class DockerRuntimeTests(unittest.TestCase):
    """Verify the containerized import and persistence workflow."""
    tmp_path: Path
    environment: dict[str, str]

    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.tmp_path = Path()
        self.environment = {}

    def setUp(self) -> None:
        """Create an isolated Compose project with a fixture workbook."""
        self.tmp_path = Path(tempfile.mkdtemp(prefix="algobotdash-docker-tests-"))
        for name in ("config", "source", "data", "reports"):
            (self.tmp_path / name).mkdir()
        root = Path(__file__).parents[1]
        shutil.copy2(root / "Dockerfile", self.tmp_path / "Dockerfile")
        shutil.copy2(root / ".dockerignore", self.tmp_path / ".dockerignore")
        shutil.copy2(root / "compose.yaml", self.tmp_path / "compose.yaml")
        for filename in (
            "pyproject.toml",
            "poetry.lock",
            "poetry.toml",
            "README.md",
            "generate_trade_report.py",
        ):
            shutil.copy2(root / filename, self.tmp_path / filename)
        shutil.copytree(root / "algobotdash", self.tmp_path / "algobotdash")
        shutil.copy2(root / "config.example.yaml", self.tmp_path / "config" / "config.yaml")
        workbook(self.tmp_path / "source" / "ReportHistory.xlsx")
        self.environment = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": f"algobotdash_test_{self.tmp_path.name.replace('-', '_')}",
            "ALGOBOTDASH_PORT": "18765",
            "ALGOBOTDASH_UID": str(os.getuid()),
            "ALGOBOTDASH_GID": str(os.getgid()),
        }

    def tearDown(self) -> None:
        """Stop the Compose project and remove its temporary workspace."""
        subprocess.run(  # nosec B603: fixed Docker Compose teardown command
            [docker_path(), "compose", "down"],
            cwd=self.tmp_path,
            env=self.environment,
            capture_output=True,
            check=False,
        )
        shutil.rmtree(self.tmp_path)

    def _compose(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run a fixed Docker Compose test command."""
        return subprocess.run(  # nosec B603: arguments are fixed test commands
            [docker_path(), "compose", *args],
            cwd=self.tmp_path,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def _health(self) -> dict[str, object]:
        """Read the local dashboard health endpoint."""
        with urllib.request.urlopen(  # nosec B310: fixed localhost HTTP test endpoint
            "http://127.0.0.1:18765/health", timeout=3
        ) as response:
            return json.load(response)

    def _wait_for_health(self) -> dict[str, object]:
        """Poll until the local dashboard reports health or times out."""
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                return self._health()
            except (OSError, urllib.error.URLError):
                time.sleep(1)
        self.fail("dashboard não ficou disponível em 45 segundos")

    def _start_and_get_health(self, *args: str) -> dict[str, object]:
        """Start Compose and wait for the dashboard health response."""
        started = self._compose(*args)
        self.assertEqual(started.returncode, 0, started.stderr)
        return self._wait_for_health()

    def test_compose_import_and_recreation_preserve_projection(self) -> None:
        """Import data and verify it survives Compose recreation."""
        config = self._compose("config")
        self.assertEqual(config.returncode, 0, config.stderr)

        initial = self._start_and_get_health("up", "-d", "--build")
        self.assertEqual(initial["status"], "ok")
        self.assertEqual(initial["configuration"], "valid")
        self.assertEqual(initial["source"], "available")
        self.assertEqual(initial["projection"], "unavailable")

        imported = self._compose(
            "run",
            "--rm",
            "algobotdash",
            "python",
            "-m",
            "algobotdash",
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        database_path = self.tmp_path / "data" / "algobotdash.sqlite"
        self.assertTrue(database_path.is_file())
        self.assertEqual(database_path.stat().st_uid, os.getuid())
        self.assertEqual(database_path.stat().st_gid, os.getgid())
        inspected = self._compose(
            "run",
            "--rm",
            "algobotdash",
            "python",
            "-c",
            "import sqlite3; "
            "connection = sqlite3.connect('/app/data/algobotdash.sqlite'); "
            "print(connection.execute('SELECT COUNT(*) FROM imports').fetchone()[0]); "
            "print(connection.execute('SELECT positions_created FROM imports').fetchone()[0]); "
            "connection.close()",
        )
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(inspected.stdout.splitlines(), ["1", "2"])

        self._compose("down").check_returncode()
        after_recreation = self._start_and_get_health("up", "-d")
        self.assertEqual(after_recreation["projection"], "available")
        self.assertTrue(after_recreation["last_imported_at"])
