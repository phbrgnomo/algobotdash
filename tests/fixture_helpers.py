"""Shared workbook fixtures for import and runtime tests."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook


def workbook(path: Path, *, ambiguous: bool = False) -> None:
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
            "2026.08.01 10:00:00", 1, "WINQ26", "buy", "2 / 3", 130000,
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
    comment = "turtle fvg" if ambiguous else "TurtleS2"
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
