"""Shared workbook fixtures for import and runtime tests."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import httpx

from openpyxl import Workbook


_POSITION_INSERT = (
    "INSERT INTO positions("
    "position_id, strategy, symbol_family, symbol_raw, direction, entry_at, exit_at, "
    "status, volume_requested, volume_executed, entry_price, exit_price, commission, "
    "swap, pnl, is_associated, import_id) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def get_asgi(app: object, path: str) -> httpx.Response:
    """Request one path from an ASGI app without opening a network port."""
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get(path)

    return asyncio.run(request())


def insert_positions(
    connection: sqlite3.Connection, rows: Iterable[tuple[object, ...]]
) -> None:
    """Insert analytical-position fixture rows using the canonical column order."""
    connection.executemany(_POSITION_INSERT, rows)


def workbook(
    path: Path, *, ambiguous: bool = False, legacy_report: bool = False
) -> None:
    """Create a representative workbook fixture at ``path``."""
    book = Workbook()
    sheet = book.active
    if sheet is None:
        raise RuntimeError("workbook fixture has no active worksheet")
    sheet.append(("Posições",))
    sheet.append(
        (
            "Horário", "Position", "Ativo", "Tipo", "Volume", "Preço", "S / L",
            "T / P", "Horário", "Preço", "Comissão", "Swap", "Lucro",
        )
    )
    sheet.append(
        (
            "2026.08.01 10:00:00", 1001 if legacy_report else 1, "WINQ26",
            "buy", "2 / 3", 130000,
            None, None, "2026.08.01 11:00:00", 130100, -2, 0, 100,
        )
    )
    sheet.append(
        (
            "2026.08.01 12:00:00", 2, "WDOU26", "sell", 1, 5400,
            None, None, None, None, -1, 0, -30,
        )
    )
    sheet.append((None, None, None, None, None, None, None, None, None, None, None, None, None))
    sheet.append(("Ordens",))
    sheet.append(
        (
            "Horário da Abertura", "Ordem", "Ativo", "Tipo", "Volume", "Preço",
            "S / L", "T / P", "Horário", "Estado", None, "Comentário",
        )
    )
    comment = "FVGscalp" if legacy_report else "turtle fvg" if ambiguous else "TurtleS2"
    sheet.append(
        (
            "2026.08.01 09:59:00", 1001, "WINQ26", "buy limit", "2 / 2",
            130000, None, None, "2026.08.01 10:00:00", "filled", None, comment,
        )
    )
    sheet.append(
        (
            "2026.08.01 11:59:00", 1002, "WDOU26", "sell limit", 1, 5400,
            None, None, "2026.08.01 12:00:00", "filled", None, None,
        )
    )
    sheet.append(("Transações",))
    sheet.append(
        (
            "Horário", "Oferta", "Ativo", "Tipo", "Direção", "Volume", "Preço",
            "Ordem", "Comissão", "Taxa", "Swap", "Lucro", "Saldo", "Comentário",
        )
    )
    sheet.append(
        (
            "2026.08.01 10:00:00", 101, "WINQ26", "buy", "in", 2, 130000,
            1001, -2, 0, 0, 0, 10000, "TurtleS2",
        )
    )
    sheet.append(
        (
            "2026.08.01 11:00:00", 102, "WINQ26", "sell", "out", 2, 130100,
            9999, -2, 0, 0, 100, 10100, None,
        )
    )
    sheet.append(
        (
            "2026.08.01 12:00:00", 103, None, "balance", None, None, None,
            None, 0, 0, 0, 5, 10105, "Ajuste de Saldo",
        )
    )
    book.save(path)
