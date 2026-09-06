"""HTTP endpoints for the local dashboard and health diagnostics."""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .config import ConfigurationError, load_config
from .environment import load_environment
from .metrics import calculate_position_metrics
from .storage import (
    PositionFilters,
    ProjectionUnavailableError,
    read_filter_options,
    read_imports,
    read_position_orders,
    read_position_metric_sample,
    read_positions,
    read_strategy_keys,
)

logger = logging.getLogger(__name__)

_ = load_environment()

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
            history, _ = read_imports(DATABASE_PATH, limit=1, offset=0)
        except ProjectionUnavailableError as exc:
            result["projection"] = "invalid"
            result["projection_error"] = _error_message(exc)
            logger.warning("não foi possível ler o histórico da projeção: %s", exc)
        else:
            result["projection"] = "available"
            if history:
                result["last_imported_at"] = history[0]["imported_at"]
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


def _optional_dimension(value: str | None, name: str) -> str | None:
    """Strip an optional dimension and reject whitespace-only input."""
    if value is None:
        return None
    if normalized := value.strip():
        return normalized
    raise HTTPException(
        status_code=422,
        detail={"code": "invalid_filter", "field": name},
    )


# The shared public contract has one argument per filter dimension.
# pylint: disable=too-many-arguments,too-many-positional-arguments
def _position_filters(
    strategy: str | None,
    symbol_family: str | None,
    direction: str | None,
    status: str,
    association: str,
    date_from: date | None,
    date_to: date | None,
) -> PositionFilters:
    """Normalize and validate filters shared by position-based endpoints."""
    normalized_strategy = _optional_dimension(strategy, "strategy")
    normalized_symbol_family = _optional_dimension(symbol_family, "symbol_family")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail={"code": "invalid_date_range"})
    if normalized_strategy is not None and association == "unassociated":
        raise HTTPException(status_code=422, detail={"code": "contradictory_filters"})
    return PositionFilters(
        strategy=normalized_strategy,
        symbol_family=normalized_symbol_family,
        direction=direction,
        status=status,
        association=association,
        date_from=date_from,
        date_to=date_to,
    )
# pylint: enable=too-many-arguments,too-many-positional-arguments


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


# FastAPI exposes each query parameter through the endpoint signature.
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
@app.get("/api/positions")
async def positions_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal[
        "opened_at", "closed_at", "realized_pnl", "symbol_family", "status"
    ] = "closed_at",
    sort_order: Literal["asc", "desc"] = "desc",
    strategy: str | None = None,
    symbol_family: str | None = None,
    direction: Literal["buy", "sell"] | None = None,
    status: Literal["closed", "open", "all"] = "closed",
    association: Literal["associated", "unassociated", "all"] = "all",
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Return a filtered page of analytical positions."""
    filters = _position_filters(
        strategy, symbol_family, direction, status, association, date_from, date_to
    )
    try:
        items, total = read_positions(
            DATABASE_PATH,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
            filters=filters,
        )
    except ProjectionUnavailableError as exc:
        raise _projection_error(exc) from exc
    return {"items": items, "total": total, "limit": limit, "offset": offset}
# pylint: enable=too-many-arguments,too-many-positional-arguments,too-many-locals


# FastAPI exposes each query parameter through the endpoint signature.
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
@app.get("/api/metrics")
async def metrics_endpoint(
    strategy: str | None = None,
    symbol_family: str | None = None,
    direction: Literal["buy", "sell"] | None = None,
    status: Literal["closed", "open", "all"] = "closed",
    association: Literal["associated", "unassociated", "all"] = "all",
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Return metrics for the filtered analytical-position sample."""
    filters = _position_filters(
        strategy, symbol_family, direction, status, association, date_from, date_to
    )
    try:
        pnl_values, excluded_open_positions = read_position_metric_sample(
            DATABASE_PATH,
            filters,
        )
    except ProjectionUnavailableError as exc:
        raise _projection_error(exc) from exc
    try:
        return calculate_position_metrics(
            pnl_values,
            excluded_open_positions=excluded_open_positions,
            realized_available=status != "open",
        )
    except OverflowError as exc:
        raise _projection_error(
            ProjectionUnavailableError("numeric overflow in position metrics")
        ) from exc
# pylint: enable=too-many-arguments,too-many-positional-arguments,too-many-locals


@app.get("/api/filter-options")
async def filter_options_endpoint() -> dict[str, list[str]]:
    """Return observed values for dynamic dashboard filters."""
    try:
        return read_filter_options(DATABASE_PATH)
    except ProjectionUnavailableError as exc:
        raise _projection_error(exc) from exc


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


@app.get("/api/strategy-keys")
async def strategy_keys_endpoint() -> dict[str, list[dict[str, str]]]:
    """Return symbol-qualified strategy identities present in the projection."""
    try:
        items = read_strategy_keys(DATABASE_PATH)
    except ProjectionUnavailableError as exc:
        raise _projection_error(exc) from exc
    return {"items": items}


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
