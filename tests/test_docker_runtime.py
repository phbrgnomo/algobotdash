from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.test_import_pipeline import workbook


def docker_compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    compose = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        check=False,
    )
    daemon = subprocess.run(["docker", "info"], capture_output=True, check=False)
    return compose.returncode == 0 and daemon.returncode == 0


@unittest.skipUnless(docker_compose_available(), "Docker Compose não está disponível")
class DockerRuntimeTests(unittest.TestCase):
    tmp_path: Path = Path()
    environment: dict[str, str] = {}

    def setUp(self) -> None:
        self.tmp_path = Path(tempfile.mkdtemp(prefix="algobotdash-docker-tests-"))
        for name in ("config", "source", "data", "reports"):
            (self.tmp_path / name).mkdir()
        root = Path(__file__).parents[1]
        shutil.copy2(root / "Dockerfile", self.tmp_path / "Dockerfile")
        shutil.copy2(root / ".dockerignore", self.tmp_path / ".dockerignore")
        shutil.copy2(root / "compose.yaml", self.tmp_path / "compose.yaml")
        for filename in ("pyproject.toml", "poetry.lock", "poetry.toml", "README.md", "generate_trade_report.py"):
            shutil.copy2(root / filename, self.tmp_path / filename)
        shutil.copytree(root / "algobotdash", self.tmp_path / "algobotdash")
        shutil.copy2(root / "config.example.yaml", self.tmp_path / "config" / "config.yaml")
        workbook(self.tmp_path / "source" / "ReportHistory.xlsx")
        self.environment = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": f"algobotdash_test_{self.tmp_path.name.replace('-', '_')}",
            "ALGOBOTDASH_PORT": "18765",
        }

    def tearDown(self) -> None:
        subprocess.run(
            ["docker", "compose", "down"],
            cwd=self.tmp_path,
            env=self.environment,
            capture_output=True,
            check=False,
        )
        shutil.rmtree(self.tmp_path)

    def _compose(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "compose", *args],
            cwd=self.tmp_path,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def _health(self) -> dict[str, object]:
        with urllib.request.urlopen("http://127.0.0.1:18765/health", timeout=3) as response:
            return json.load(response)

    def _wait_for_health(self) -> dict[str, object]:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                return self._health()
            except (OSError, urllib.error.URLError):
                time.sleep(1)
        self.fail("dashboard não ficou disponível em 45 segundos")

    def test_compose_import_and_recreation_preserve_projection(self) -> None:
        config = self._compose("config")
        self.assertEqual(config.returncode, 0, config.stderr)

        started = self._compose("up", "-d", "--build")
        self.assertEqual(started.returncode, 0, started.stderr)
        initial = self._wait_for_health()
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
            "--config",
            "/app/config/config.yaml",
            "--database",
            "/app/data/algobotdash.sqlite",
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertTrue((self.tmp_path / "data" / "algobotdash.sqlite").is_file())

        self._compose("down").check_returncode()
        recreated = self._compose("up", "-d")
        self.assertEqual(recreated.returncode, 0, recreated.stderr)
        after_recreation = self._wait_for_health()
        self.assertEqual(after_recreation["projection"], "available")
