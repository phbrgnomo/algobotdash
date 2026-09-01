"""Load the project's local environment file without overriding process values."""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

DEFAULT_ENV_PATH = Path(".env")
ENV_FILE_VARIABLE = "ALGOBOTDASH_ENV_FILE"
_ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvironmentFileError(ValueError):
    """Raised when a local environment file has invalid syntax."""


def load_environment(
    path: str | Path | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> Path | None:
    """Load simple KEY=VALUE entries, preserving values already in the process."""
    target = os.environ if environ is None else environ
    environment_path = Path(path or target.get(ENV_FILE_VARIABLE, DEFAULT_ENV_PATH))
    if not environment_path.is_file():
        return None
    try:
        lines = environment_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise EnvironmentFileError(
            f"não foi possível ler o arquivo de ambiente {environment_path}: {exc}"
        ) from exc

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not _ENVIRONMENT_KEY.fullmatch(key):
            raise EnvironmentFileError(
                f"entrada inválida em {environment_path}:{line_number}"
            )
        _ = target.setdefault(
            key,
            _environment_value(raw_value.strip(), environment_path, line_number),
        )
    return environment_path


def _environment_value(value: str, path: Path, line_number: int) -> str:
    """Remove matching quotes and reject an unmatched quoted value."""
    if not value or value[0] not in {'"', "'"}:
        return value
    if len(value) < 2 or value[-1] != value[0]:
        raise EnvironmentFileError(f"aspas inválidas em {path}:{line_number}")
    return value[1:-1]
