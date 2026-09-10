"""Tests for local environment-file loading."""

from __future__ import annotations

import tempfile
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

from algobotdash.__main__ import main
from algobotdash.environment import EnvironmentFileError, load_environment
from tests.fixture_helpers import workbook


class EnvironmentTests(unittest.TestCase):
    """Verify deterministic and safe .env loading semantics."""

    def _load_with_process_config(self, contents: str) -> dict[str, str]:
        """Load one fixture while preserving the explicit process config path."""
        with tempfile.TemporaryDirectory(prefix="algobotdash-env-tests-") as raw_dir:
            path = Path(raw_dir) / ".env"
            _ = path.write_text(contents, encoding="utf-8")
            environment = {"ALGOBOTDASH_CONFIG": "process-config.yaml"}

            loaded = load_environment(path, environ=environment)

        self.assertEqual(loaded, path)
        self.assertEqual(environment["ALGOBOTDASH_CONFIG"], "process-config.yaml")
        return environment

    def test_loads_values_without_overriding_process_environment(self) -> None:
        """Explicit process values must take precedence over the local file."""
        environment = self._load_with_process_config(
            "# local configuration\n"
            "ALGOBOTDASH_CONFIG=file-config.yaml\n"
            "export ALGOBOTDASH_DATABASE='data/local.sqlite'\n"
            'QUOTED_VALUE="value with spaces"'
        )
        self.assertEqual(environment["ALGOBOTDASH_DATABASE"], "data/local.sqlite")
        self.assertEqual(environment["QUOTED_VALUE"], "value with spaces")

    def test_existing_process_value_skips_invalid_file_value(self) -> None:
        """A process value takes precedence without parsing its file fallback."""
        environment = self._load_with_process_config('ALGOBOTDASH_CONFIG="unterminated\n')
        self.assertEqual(environment["ALGOBOTDASH_CONFIG"], "process-config.yaml")

    def test_missing_file_is_optional(self) -> None:
        """Installed runtimes may rely entirely on process environment variables."""
        environment: dict[str, str] = {}

        loaded = load_environment("does-not-exist.env", environ=environment)

        self.assertIsNone(loaded)
        self.assertEqual(environment, {})

    def test_rejects_invalid_entries(self) -> None:
        """Malformed local configuration should fail with its source line."""
        with tempfile.TemporaryDirectory(prefix="algobotdash-env-tests-") as raw_dir:
            path = Path(raw_dir) / ".env"
            _ = path.write_text("INVALID ENTRY\n", encoding="utf-8")

            with self.assertRaisesRegex(EnvironmentFileError, r"\.env:1"):
                _ = load_environment(path, environ={})

    def test_rejects_unmatched_quotes(self) -> None:
        """Quoted values must terminate on the same line."""
        with tempfile.TemporaryDirectory(prefix="algobotdash-env-tests-") as raw_dir:
            path = Path(raw_dir) / ".env"
            _ = path.write_text('BROKEN="value\n', encoding="utf-8")

            with self.assertRaisesRegex(EnvironmentFileError, r"aspas inválidas"):
                _ = load_environment(path, environ={})

    def test_import_cli_uses_paths_from_environment_file(self) -> None:
        """The import entrypoint should work without explicit path arguments."""
        with tempfile.TemporaryDirectory(prefix="algobotdash-env-cli-tests-") as raw_dir:
            directory = Path(raw_dir)
            source = directory / "ReportHistory.xlsx"
            config = directory / "config.yaml"
            database = directory / "algobotdash.sqlite"
            environment_path = directory / ".env"
            workbook(source)
            _ = config.write_text(f"source:\n  path: {source}\n", encoding="utf-8")
            _ = environment_path.write_text(
                f"ALGOBOTDASH_CONFIG={config}\nALGOBOTDASH_DATABASE={database}\n",
                encoding="utf-8",
            )

            with patch.dict(
                environ,
                {"ALGOBOTDASH_ENV_FILE": str(environment_path)},
                clear=True,
            ), patch("sys.argv", ["algobotdash"]), patch("builtins.print"):
                main()

            self.assertTrue(database.is_file())
