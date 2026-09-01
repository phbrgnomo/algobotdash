"""HTTP endpoints for the local dashboard and health diagnostics."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .config import ConfigurationError, load_config
from .storage import (
    ProjectionUnavailableError,
    read_import_history,
    read_imports,
    read_position_orders,
    read_positions,
)

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
    except ConfigurationError as exc:
        result["error"] = _error_message(exc)
        logger.warning(
            "configuração indisponível: %s",
            exc,
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )
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
    if (
        result["configuration"] != "valid"
        or result["source"] == "missing"
        or result["projection"] == "invalid"
    ):
        result["status"] = "error"
    return result


def health() -> dict[str, Any]:
    """Return the current configuration, source, and projection state."""
    return _health_state()


def _projection_error(_exc: ProjectionUnavailableError) -> HTTPException:
    """Translate projection access failures into one stable HTTP response."""
    return HTTPException(
        status_code=503,
        detail={"code": "projection_unavailable"},
    )


def _status_state() -> dict[str, Any]:
    """Return dashboard-facing source and projection state without failing HTTP."""
    health_state = _health_state()
    source_name: str | None = None
    try:
        config = load_config(CONFIG_PATH)
    except ConfigurationError:
        pass
    else:
        source_name = config.source_path.name

    last_import: dict[str, Any] | None = None
    if health_state["projection"] == "available":
        try:
            history, _ = read_imports(DATABASE_PATH, limit=1, offset=0)
        except ProjectionUnavailableError:
            health_state["projection"] = "invalid"
        else:
            if history:
                latest_import = history[0]
                last_import = {
                    "source_name": latest_import["source_name"],
                    "source_hash": latest_import["source_hash"],
                    "imported_at": latest_import["imported_at"],
                }

    projection = health_state["projection"]
    state = "ready" if last_import else "empty" if projection == "available" else "unavailable"
    return {
        "state": state,
        "configuration": health_state["configuration"],
        "source": health_state["source"],
        "projection": projection,
        "source_name": source_name,
        "last_import": last_import,
    }


def dashboard() -> FileResponse:
    """Serve the dashboard's static HTML page."""
    return FileResponse(STATIC_INDEX, media_type="text/html")


@app.get("/health", response_model=None)
async def health_endpoint() -> dict[str, Any] | JSONResponse:
    """Expose health diagnostics over HTTP without thread-pool dispatch."""
    payload = health()
    return JSONResponse(
        content=payload,
        status_code=200 if payload["status"] == "ok" else 503,
    )


@app.get("/api/positions")
async def positions_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal[
        "opened_at", "closed_at", "realized_pnl", "symbol_family", "status"
    ] = "closed_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> dict[str, Any]:
    """Return a page of analytical positions with validated ordering."""
    try:
        items, total = read_positions(
            DATABASE_PATH,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except ProjectionUnavailableError as exc:
        raise _projection_error(exc) from exc
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/positions/{position_id}/orders")
async def position_orders_endpoint(position_id: str) -> dict[str, list[dict[str, Any]]]:
    """Return auditable orders belonging to a report position identifier."""
    try:
        orders = read_position_orders(DATABASE_PATH, position_id)
    except ProjectionUnavailableError as exc:
        raise _projection_error(exc) from exc
    if orders is None:
        raise HTTPException(status_code=404, detail={"code": "position_not_found"})
    return {"items": orders}


@app.get("/api/strategies")
async def strategies_endpoint() -> dict[str, list[dict[str, str]]]:
    """Return configured strategy group identities without exposing patterns."""
    try:
        config = load_config(CONFIG_PATH)
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "configuration_unavailable"},
        ) from exc
    return {"items": [{"name": group.name} for group in config.strategy_groups]}


@app.get("/api/imports")
async def imports_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return valid import history in reverse chronological order."""
    try:
        items, total = read_imports(DATABASE_PATH, limit=limit, offset=offset)
    except ProjectionUnavailableError as exc:
        raise _projection_error(exc) from exc
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/status")
async def status_endpoint() -> dict[str, Any]:
    """Return dashboard state even while the projection is unavailable."""
    return _status_state()


@app.get("/")
async def dashboard_endpoint() -> HTMLResponse:
    """Expose the static dashboard page over HTTP."""
    return HTMLResponse(STATIC_INDEX.read_text(encoding="utf-8"))
