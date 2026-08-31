from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse

from .config import load_config
from .storage import read_import_history

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"
CONFIG_PATH = Path(os.getenv("ALGOBOTDASH_CONFIG", "config/config.yaml"))
DATABASE_PATH = Path(os.getenv("ALGOBOTDASH_DATABASE", "data/algobotdash.sqlite"))
STATIC_INDEX = Path(__file__).parent / "static" / "index.html"

app = FastAPI(title="algobotdash", version=APP_VERSION)


def _error_message(exc: Exception) -> str:
    return str(exc).splitlines()[0][:240] or exc.__class__.__name__


def _health_state() -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "ok",
        "version": APP_VERSION,
        "configuration": "invalid",
        "source": "unknown",
        "projection": "unavailable",
    }

    try:
        config = load_config(CONFIG_PATH)
    except Exception as exc:
        result["error"] = _error_message(exc)
        logger.warning("configuração indisponível: %s", exc, exc_info=logger.isEnabledFor(logging.DEBUG))
    else:
        result["configuration"] = "valid"
        result["source"] = "available" if config.source_path.is_file() else "missing"

    if DATABASE_PATH.is_file():
        try:
            history = read_import_history(DATABASE_PATH)
        except (OSError, sqlite3.Error, ValueError) as exc:
            result["projection"] = "invalid"
            result["projection_error"] = _error_message(exc)
            logger.warning("não foi possível ler o histórico da projeção: %s", exc)
        else:
            result["projection"] = "available"
            if history:
                result["last_imported_at"] = history[-1][3]
    return result


@app.get("/health")
def health() -> dict[str, Any]:
    return _health_state()


@app.get("/", response_class=FileResponse)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_INDEX, media_type="text/html")
