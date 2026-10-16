"""HTTP endpoints for the local dashboard and health diagnostics."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .config import ConfigurationError, load_config
from .environment import load_environment
from .metrics import (
    calculate_monetary_drawdown,
    calculate_percentage_drawdown,
    calculate_position_metrics,
    calculate_temporal_metrics,
    effective_metric_period,
)
from .refresh import RefreshInProgressError, RefreshLockUnavailableError
from .refresh_runner import RefreshRunner
from .series import MAX_SERIES_ROWS, calculate_chart_series
from .series_limits import SeriesBudget, SeriesBusyError, series_budget
from .storage import (
    PositionFilters,
    ProjectionUnavailableError,
    ProjectionChangedError,
    SampleTooLargeError,
    read_filter_options,
    read_imports,
    read_metric_sample,
    read_position_orders,
    read_positions,
    read_projection_metadata,
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


def _allowed_refresh_origin(origin: str | None) -> bool:
    """Accept only explicit, exact local HTTP origins."""
    if origin is None:
        return False
    try:
        parsed = urlsplit(origin)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _health_state() -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "ok",
        "version": APP_VERSION,
        "configuration": "invalid",
        "source": "unknown",
        "projection": "unavailable",
        "timezone": None,
        "projection_revision": None,
    }

    config = None
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
        result["timezone"] = config.timezone.key
        result["source"] = "available" if config.source_path.is_file() else "missing"

    if DATABASE_PATH.is_file() and config is not None:
        try:
            history, _ = read_imports(
                DATABASE_PATH, limit=1, offset=0, analysis_timezone=config.timezone
            )
            metadata = read_projection_metadata(DATABASE_PATH, config.timezone)
        except ProjectionUnavailableError as exc:
            result["projection"] = "invalid"
            result["projection_error"] = _error_message(exc)
            logger.warning("não foi possível ler o histórico da projeção: %s", exc)
        else:
            result["projection"] = "available"
            result["projection_revision"] = metadata.revision
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


def _projection_error(exc: ProjectionUnavailableError) -> HTTPException:
    """Translate projection access failures into one stable HTTP response."""
    if isinstance(exc, ProjectionChangedError):
        return HTTPException(status_code=409, detail={"code": "projection_revision_changed"})
    return HTTPException(
        status_code=503,
        detail={"code": "projection_unavailable"},
    )


def _runtime_config():
    """Load the mandatory timezone configuration for analytical endpoints."""
    try:
        return load_config(CONFIG_PATH)
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "configuration_unavailable"}
        ) from exc


def _optional_dimension(values: list[str] | None, name: str) -> tuple[str, ...] | None:
    """Normalize repeated dimension values without conflating omission and empty."""
    if values is None:
        return None
    normalized = tuple(dict.fromkeys(value.strip() for value in values))
    if normalized and all(normalized) and len(normalized) <= 500:
        return normalized
    raise HTTPException(
        status_code=422,
        detail={"code": "invalid_filter", "field": name},
    )


# The shared public contract has one argument per filter dimension.
# pylint: disable=too-many-arguments,too-many-positional-arguments
async def _position_filters(
    strategy: Annotated[list[str] | None, Query()] = None,
    symbol_family: Annotated[list[str] | None, Query()] = None,
    direction: Literal["buy", "sell"] | None = None,
    status: Literal["closed", "open", "all"] = "closed",
    association: Literal["associated", "unassociated", "all"] = "all",
    date_from: date | None = None,
    date_to: date | None = None,
    include_unclassified: bool = False,
    selection_empty: bool = False,
    projection_revision: str | None = None,
) -> PositionFilters:
    """Normalize and validate filters shared by position-based endpoints."""
    normalized_strategy = _optional_dimension(strategy, "strategy")
    normalized_symbol_family = _optional_dimension(symbol_family, "symbol_family")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail={"code": "invalid_date_range"})
    if (normalized_strategy is not None and association == "unassociated"
            and not include_unclassified and not selection_empty):
        raise HTTPException(status_code=422, detail={"code": "contradictory_filters"})
    return PositionFilters(
        strategy=normalized_strategy,
        symbol_family=normalized_symbol_family,
        direction=direction,
        status=status,
        association=association,
        date_from=date_from,
        date_to=date_to,
        include_unclassified=include_unclassified,
        selection_empty=selection_empty,
        projection_revision=projection_revision,
    )
# pylint: enable=too-many-arguments,too-many-positional-arguments


def _status_state() -> dict[str, Any]:
    """Return dashboard-facing source and projection state without failing HTTP."""
    health_state = _health_state()
    source_name: str | None = None
    config = None
    try:
        config = load_config(CONFIG_PATH)
    except ConfigurationError:
        pass
    else:
        source_name = config.source_path.name

    last_import: dict[str, Any] | None = None
    if health_state["projection"] == "available" and config is not None:
        try:
            history, _ = read_imports(
                DATABASE_PATH, limit=1, offset=0, analysis_timezone=config.timezone
            )
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
    refresh_operation: dict[str, Any] | None = None
    try:
        latest_refresh = RefreshRunner(CONFIG_PATH, DATABASE_PATH).latest()
    except (OSError, ValueError, sqlite3.Error, RefreshLockUnavailableError):
        logger.exception("não foi possível ler o estado das atualizações")
    else:
        if latest_refresh is not None:
            refresh_operation = latest_refresh.as_dict()
    return {
        "state": state,
        "configuration": health_state["configuration"],
        "source": health_state["source"],
        "projection": projection,
        "source_name": source_name,
        "last_import": last_import,
        "timezone": health_state["timezone"],
        "projection_revision": health_state["projection_revision"],
        "refresh_operation": refresh_operation,
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
    filters: Annotated[PositionFilters, Depends(_position_filters)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal[
        "opened_at", "closed_at", "realized_pnl", "symbol_family", "status"
    ] = "closed_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> dict[str, Any]:
    """Return a filtered page of analytical positions."""
    config = _runtime_config()
    try:
        items, total = read_positions(
            DATABASE_PATH,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
            analysis_timezone=config.timezone,
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
    filters: Annotated[PositionFilters, Depends(_position_filters)],
) -> dict[str, Any]:
    """Return metrics for the filtered analytical-position sample."""
    config = _runtime_config()
    status, date_from, date_to = filters.status, filters.date_from, filters.date_to
    try:
        sample = read_metric_sample(DATABASE_PATH, filters, config.timezone)
    except ProjectionUnavailableError as exc:
        raise _projection_error(exc) from exc
    try:
        payload = calculate_position_metrics(
            sample.pnl_values,
            excluded_open_positions=sample.excluded_open_positions,
            realized_available=status != "open",
        )
        temporal = calculate_temporal_metrics(
            sample.daily_pnl,
            sample.opening_balances,
            date_from=date_from,
            date_to=date_to,
            global_first_closed_date=sample.global_first_closed_date,
            global_last_closed_date=sample.global_last_closed_date,
            realized_available=status != "open",
        )
        temporal_reasons = cast(
            dict[str, str], temporal.pop("temporal_unavailable_reasons")
        )
        payload.update(temporal)
        position_reasons = cast(dict[str, str], payload["unavailable_reasons"])
        payload["temporal_unavailable_reasons"] = temporal_reasons
        period = effective_metric_period(
            sample.daily_pnl, date_from=date_from, date_to=date_to,
            global_first_closed_date=sample.global_first_closed_date,
            global_last_closed_date=sample.global_last_closed_date,
        )
        drawdown = calculate_monetary_drawdown(
            sample.closed_events, period=period,
            realized_available=status != "open",
            analysis_timezone=config.timezone,
        )
        payload["monetary_drawdown"] = drawdown
        if drawdown["state"] == "unavailable":
            position_reasons["monetary_drawdown"] = (
                "realized_metrics_unavailable_for_open_status"
                if status == "open" else "numeric_overflow"
            )
        percentage, percentage_reason = calculate_percentage_drawdown(
            sample.closed_events, sample.opening_balances,
            period=period, realized_available=status != "open",
            analysis_timezone=config.timezone,
        )
        payload["percentage_drawdown"] = percentage
        payload["projection_revision"] = sample.projection_revision
        if percentage_reason is not None:
            position_reasons["percentage_drawdown"] = percentage_reason
        return payload
    except OverflowError as exc:
        raise _projection_error(
            ProjectionUnavailableError("numeric overflow in position metrics")
        ) from exc
# pylint: enable=too-many-arguments,too-many-positional-arguments,too-many-locals


def _series_response(
    filters: PositionFilters, granularity: str, budget: SeriesBudget
) -> JSONResponse:
    """Read, calculate and serialize off the event loop, retaining the work permit."""
    try:
        config = _runtime_config()
        sample = read_metric_sample(
            DATABASE_PATH, filters, config.timezone, max_rows=MAX_SERIES_ROWS
        )
        return JSONResponse(calculate_chart_series(sample, filters, config.timezone, granularity))
    except SampleTooLargeError as exc:
        raise HTTPException(status_code=413, detail={"code": "series_too_large"}) from exc
    except ProjectionUnavailableError as exc:
        raise _projection_error(exc) from exc
    finally:
        budget.finish()


@app.get("/api/series", response_model=None)
async def series_endpoint(
    filters: Annotated[PositionFilters, Depends(_position_filters)],
    granularity: Literal["day", "week", "month", "year"] = "day",
) -> JSONResponse:
    """Return unpaginated realized series using the shared analytical sample."""
    budget = series_budget(str(DATABASE_PATH.resolve()))
    try:
        budget.start()
    except SeriesBusyError as exc:
        raise HTTPException(
            status_code=429, detail={"code": "series_rate_limit"},
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    # Shield keeps the admitted worker alive until its finally block releases
    # concurrency, even when the browser aborts the HTTP request.
    return await asyncio.shield(asyncio.to_thread(_series_response, filters, granularity, budget))


@app.get("/static/charts.js", include_in_schema=False)
async def chart_script() -> FileResponse:
    """Serve the local, dependency-free chart renderer."""
    return FileResponse(STATIC_INDEX.parent / "charts.js", media_type="text/javascript")


@app.get("/api/filter-options")
async def filter_options_endpoint() -> dict[str, list[str]]:
    """Return observed values for dynamic dashboard filters."""
    try:
        return read_filter_options(DATABASE_PATH, _runtime_config().timezone)
    except ProjectionUnavailableError as exc:
        raise _projection_error(exc) from exc


@app.get("/api/positions/{position_id}/orders")
async def position_orders_endpoint(position_id: str) -> dict[str, list[dict[str, Any]]]:
    """Return auditable orders belonging to a report position identifier."""
    try:
        orders = read_position_orders(
            DATABASE_PATH, position_id, _runtime_config().timezone
        )
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
        items = read_strategy_keys(DATABASE_PATH, _runtime_config().timezone)
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
        items, total = read_imports(
            DATABASE_PATH,
            limit=limit,
            offset=offset,
            analysis_timezone=_runtime_config().timezone,
        )
    except ProjectionUnavailableError as exc:
        raise _projection_error(exc) from exc
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/status")
async def status_endpoint() -> dict[str, Any]:
    """Return dashboard state even while the projection is unavailable."""
    return _status_state()


@app.post("/api/refresh", status_code=202)
async def start_refresh_endpoint(
    request: Request,
    request_marker: Annotated[str | None, Header(alias="X-Algobotdash-Request")] = None,
) -> JSONResponse:
    """Start one durable background refresh."""
    origin = request.headers.get("origin")
    if request_marker != "refresh" or not _allowed_refresh_origin(origin):
        raise HTTPException(
            status_code=403,
            detail={"code": "refresh_request_forbidden"},
        )
    try:
        runner = RefreshRunner(CONFIG_PATH, DATABASE_PATH)
        operation = runner.start()
    except RefreshInProgressError as exc:
        active_id = None
        try:
            latest = RefreshRunner(CONFIG_PATH, DATABASE_PATH).latest()
        except (OSError, ValueError, sqlite3.Error):
            latest = None
        if latest is not None and latest.state in {"queued", "running"}:
            active_id = latest.operation_id
        raise HTTPException(
            status_code=409,
            detail={"code": "refresh_in_progress", "operation_id": active_id},
        ) from exc
    except (
        OSError,
        ValueError,
        sqlite3.Error,
        RefreshLockUnavailableError,
    ) as exc:
        logger.exception("não foi possível registrar a atualização")
        raise HTTPException(
            status_code=503,
            detail={"code": "refresh_state_unavailable"},
        ) from exc
    return JSONResponse(
        status_code=202,
        content={
            **operation.as_dict(),
            "status_url": f"/api/refresh/{operation.operation_id}",
        },
    )


@app.get("/api/refresh/{operation_id}")
async def refresh_operation_endpoint(operation_id: str) -> dict[str, Any]:
    """Return one persisted background refresh attempt."""
    try:
        operation = RefreshRunner(CONFIG_PATH, DATABASE_PATH).get(operation_id)
    except (
        OSError,
        ValueError,
        sqlite3.Error,
        RefreshLockUnavailableError,
    ) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "refresh_state_unavailable"},
        ) from exc
    if operation is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "refresh_operation_not_found"},
        )
    return operation.as_dict()


@app.get("/")
async def dashboard_endpoint() -> HTMLResponse:
    """Expose the static dashboard page over HTTP."""
    return HTMLResponse(STATIC_INDEX.read_text(encoding="utf-8"))
