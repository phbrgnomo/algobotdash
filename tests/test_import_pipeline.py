from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from algobotdash.config import (
    ConfigurationError,
    ImportConfig,
    StrategyGroup,
    load_config,
)
from algobotdash.parser import _number
from algobotdash.service import ImportService


def config(source: Path) -> ImportConfig:
    return ImportConfig(
        source_path=source,
        symbol_prefixes=(("WIN", "WIN"), ("WDO", "WDO")),
        strategy_groups=(
            StrategyGroup("Turtle", ("turtle",)),
            StrategyGroup("FVG", ("fvg",)),
        ),
    )


def workbook(path: Path, *, ambiguous: bool = False) -> None:
    book = Workbook()
    sheet = book.active
    if sheet is None:
        raise RuntimeError("workbook fixture has no active worksheet")
    sheet.append(("Posições",))
    sheet.append(("Horário", "Position", "Ativo", "Tipo", "Volume", "Preço", "S / L", "T / P", "Horário", "Preço", "Comissão", "Swap", "Lucro"))
    sheet.append(("2026.08.01 10:00:00", 1, "WINQ26", "buy", "2 / 3", 130000, None, None, "2026.08.01 11:00:00", 130100, -2, 0, 100))
    sheet.append(("2026.08.01 12:00:00", 2, "WDOU26", "sell", 1, 5400, None, None, None, None, -1, 0, -30))
    sheet.append((None, None, None, None, None, None, None, None, None, None, None, None, None))
    sheet.append(("Ordens",))
    sheet.append(("Horário da Abertura", "Ordem", "Ativo", "Tipo", "Volume", "Preço", "S / L", "T / P", "Horário", "Estado", None, "Comentário"))
    comment = "turtle fvg" if ambiguous else "TurtleS2"
    sheet.append(("2026.08.01 09:59:00", 1001, "WINQ26", "buy limit", "2 / 2", 130000, None, None, "2026.08.01 10:00:00", "filled", None, comment))
    sheet.append(("2026.08.01 11:59:00", 1002, "WDOU26", "sell limit", "1 / 1", 5400, None, None, "2026.08.01 12:00:00", "filled", None, None))
    sheet.append(("Transações",))
    sheet.append(("Horário", "Oferta", "Ativo", "Tipo", "Direção", "Volume", "Preço", "Ordem", "Comissão", "Taxa", "Swap", "Lucro", "Saldo", "Comentário"))
    sheet.append(("2026.08.01 10:00:00", 101, "WINQ26", "buy", "in", 2, 130000, 1001, -2, 0, 0, 0, 10000, "TurtleS2"))
    sheet.append(("2026.08.01 11:00:00", 102, "WINQ26", "sell", "out", 2, 130100, 9999, -2, 0, 0, 100, 10100, None))
    sheet.append(("2026.08.01 12:00:00", 103, None, "balance", None, None, None, None, 0, 0, 0, 5, 10105, "Ajuste de Saldo"))
    book.save(path)


def remove_section_label(path: Path, section: str) -> None:
    book = load_workbook(path)
    sheet = book.active
    if sheet is None:
        raise RuntimeError("workbook fixture has no active worksheet")
    for row in range(1, sheet.max_row + 1):
        if sheet.cell(row, 1).value == section:
            sheet.cell(row, 1).value = None
            book.save(path)
            return
    raise AssertionError(f"section not found: {section}")


def truncate_positions_header(path: Path) -> None:
    book = load_workbook(path)
    sheet = book.active
    if sheet is None:
        raise RuntimeError("workbook fixture has no active worksheet")
    for row in range(1, sheet.max_row + 1):
        if sheet.cell(row, 1).value == "Posições":
            sheet.cell(row + 1, 13).value = None
            book.save(path)
            return
    raise AssertionError("section not found: Posições")


def append_rejected_transaction(path: Path) -> None:
    book = load_workbook(path)
    sheet = book.active
    if sheet is None:
        raise RuntimeError("workbook fixture has no active worksheet")
    sheet.append(("2026.08.01 13:00:00", None, "WINQ26", "sell", "out", 1, 130100, 1001, 0, 0, 0, 0, 10105, "invalid transaction"))
    book.save(path)


class ImportPipelineTests(unittest.TestCase):
  tmp_path: Path = Path()

  def setUp(self) -> None:
    self.tmp_path = Path(tempfile.mkdtemp(prefix="algobotdash-tests-"))
    self.addCleanup(shutil.rmtree, self.tmp_path)

  def test_refresh_reconstructs_positions_orders_and_quality_counts(self) -> None:
    tmp_path = self.tmp_path
    source = tmp_path / "history.xlsx"
    database = tmp_path / "data" / "trades.sqlite"
    workbook(source)

    result = ImportService(config(source)).refresh(database)

    self.assertEqual(result.positions_created, 2)
    self.assertEqual(result.no_comment_count, 2)
    self.assertEqual(result.rejected_count, 0)
    connection = sqlite3.connect(database)
    self.assertEqual(connection.execute("select count(*) from positions").fetchone()[0], 2)
    self.assertEqual(connection.execute("select count(*) from orders").fetchone()[0], 2)
    self.assertEqual(connection.execute("select count(*) from transactions").fetchone()[0], 3)
    self.assertEqual(connection.execute("select count(*) from transactions where direction = 'balance'").fetchone()[0], 1)
    self.assertEqual(connection.execute("select volume_requested, volume_executed from positions where position_id = '1'").fetchone(), (2.0, 3.0))
    self.assertEqual(connection.execute("select volume_requested, volume_executed from orders where order_id = '1001'").fetchone(), (2.0, 2.0))
    self.assertIsNone(connection.execute("select strategy from positions where position_id = '1'").fetchone()[0])
    self.assertIsNone(connection.execute("select position_id from orders where order_id = '1001'").fetchone()[0])
    self.assertEqual(connection.execute("select strategy from orders where order_id = '1001'").fetchone()[0], "Turtle")
    self.assertEqual(connection.execute("select strategy from transactions where transaction_id = '101'").fetchone()[0], "Turtle")
    self.assertIsNone(connection.execute("select position_id from transactions where transaction_id = '101'").fetchone()[0])
    self.assertEqual(connection.execute("select symbol_family from positions where position_id = '1'").fetchone()[0], "WIN")
    self.assertEqual(connection.execute("select status from positions where position_id = '2'").fetchone()[0], "open")
    self.assertEqual(connection.execute("select no_comment_count, rejected_count from imports").fetchone(), (2, 0))
    connection.close()


  def test_configuration_uses_longest_symbol_prefix(self) -> None:
    configured = ImportConfig(
        source_path=Path("history.xlsx"),
        symbol_prefixes=(("W", "W"), ("WIN", "WIN")),
        strategy_groups=(),
    )

    self.assertEqual(configured.normalize_symbol("WIN$"), "WIN")


  def test_configuration_rejects_invalid_patterns(self) -> None:
    for patterns in ("turtle", [], [""], ["turtle", 1]):
      with self.subTest(patterns=patterns):
        path = self.tmp_path / "config.yml"
        path.write_text(
            "source:\n  path: history.xlsx\nstrategies:\n  groups:\n"
            f"    - name: Turtle\n      patterns: {patterns!r}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ConfigurationError, "patterns deve ser"):
          load_config(path)


  def test_number_rejects_non_finite_values(self) -> None:
    self.assertEqual(_number("1.5"), 1.5)
    self.assertIsNone(_number("nan"))
    self.assertIsNone(_number("inf"))
    self.assertIsNone(_number("-inf"))


  def test_new_source_preserves_import_history(self) -> None:
    tmp_path = self.tmp_path
    source = tmp_path / "history.xlsx"
    changed_source = tmp_path / "history-changed.xlsx"
    database = tmp_path / "trades.sqlite"
    workbook(source)
    workbook(changed_source)
    changed_book = load_workbook(changed_source)
    changed_sheet = changed_book.active
    if changed_sheet is None:
        raise RuntimeError("workbook fixture has no active worksheet")
    changed_sheet["M3"] = 101
    changed_book.save(changed_source)

    ImportService(config(source)).refresh(database)
    ImportService(config(changed_source)).refresh(database)

    connection = sqlite3.connect(database)
    self.assertEqual(connection.execute("select count(*) from imports").fetchone()[0], 2)
    self.assertEqual(connection.execute("select count(*) from positions").fetchone()[0], 2)
    self.assertEqual(connection.execute("select count(*) from orders").fetchone()[0], 2)
    self.assertEqual(connection.execute("select count(*) from transactions").fetchone()[0], 3)
    self.assertEqual(connection.execute("select count(*) from rejected_rows").fetchone()[0], 0)
    self.assertEqual(connection.execute("select strategy from transactions where transaction_id = '101'").fetchone()[0], "Turtle")
    connection.close()


  def test_refresh_rejects_unsupported_schema_version(self) -> None:
    tmp_path = self.tmp_path
    source = tmp_path / "history.xlsx"
    database = tmp_path / "trades.sqlite"
    workbook(source)
    connection = sqlite3.connect(database)
    connection.execute("create table schema_version (version integer primary key)")
    connection.execute("insert into schema_version values (99)")
    connection.execute("create table imports (id integer primary key)")
    connection.commit()
    connection.close()

    with self.assertRaisesRegex(ValueError, "versão de schema SQLite não suportada"):
      ImportService(config(source)).refresh(database)


  def test_refresh_rejects_malformed_report_without_mutating_projection(self) -> None:
    tmp_path = self.tmp_path
    source = tmp_path / "history.xlsx"
    database = tmp_path / "trades.sqlite"
    workbook(source)
    ImportService(config(source)).refresh(database)

    malformed_sources = {
        "missing_positions": ("missing_positions.xlsx", "Posições", False),
        "missing_orders": ("missing_orders.xlsx", "Ordens", False),
        "missing_transactions": ("missing_transactions.xlsx", "Transações", False),
        "truncated_header": ("truncated_header.xlsx", "", True),
    }
    for name, (filename, section, truncated) in malformed_sources.items():
        with self.subTest(name=name):
            malformed = tmp_path / filename
            workbook(malformed)
            if truncated:
                truncate_positions_header(malformed)
            else:
                remove_section_label(malformed, section)

            with self.assertRaises(ValueError):
                ImportService(config(malformed)).refresh(database)

            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("select count(*) from positions").fetchone()[0], 2)
            self.assertEqual(connection.execute("select count(*) from orders").fetchone()[0], 2)
            self.assertEqual(connection.execute("select count(*) from transactions").fetchone()[0], 3)
            self.assertEqual(connection.execute("select count(*) from rejected_rows").fetchone()[0], 0)
            self.assertEqual(connection.execute("select strategy from orders where order_id = '1001'").fetchone()[0], "Turtle")
            self.assertEqual(connection.execute("select strategy from transactions where transaction_id = '101'").fetchone()[0], "Turtle")
            connection.close()


  def test_refresh_is_idempotent_by_replacing_projection(self) -> None:
    tmp_path = self.tmp_path
    source = tmp_path / "history.xlsx"
    database = tmp_path / "trades.sqlite"
    workbook(source)
    append_rejected_transaction(source)
    service = ImportService(config(source))

    first = service.refresh(database)
    second = service.refresh(database)

    self.assertEqual(first.source_hash, second.source_hash)
    connection = sqlite3.connect(database)
    self.assertEqual(connection.execute("select count(*) from imports").fetchone()[0], 1)
    self.assertEqual(connection.execute("select count(*) from positions").fetchone()[0], 2)
    self.assertEqual(connection.execute("select count(*) from orders").fetchone()[0], 2)
    self.assertEqual(connection.execute("select count(*) from transactions").fetchone()[0], 3)
    self.assertEqual(connection.execute("select count(*) from rejected_rows").fetchone()[0], 1)
    self.assertEqual(connection.execute("select reason from rejected_rows").fetchone()[0], "campos obrigatórios inválidos em transação")
    self.assertEqual(connection.execute("select strategy from orders where order_id = '1001'").fetchone()[0], "Turtle")
    self.assertEqual(connection.execute("select strategy from transactions where transaction_id = '101'").fetchone()[0], "Turtle")
    connection.close()


  def test_ambiguous_comment_aborts_without_publishing_or_mutating_existing_projection(self) -> None:
    tmp_path = self.tmp_path
    source = tmp_path / "history.xlsx"
    bad_source = tmp_path / "bad.xlsx"
    database = tmp_path / "trades.sqlite"
    workbook(source)
    workbook(bad_source, ambiguous=True)
    ImportService(config(source)).refresh(database)

    with self.assertRaisesRegex(ConfigurationError, "múltiplos grupos"):
        ImportService(config(bad_source)).refresh(database)

    connection = sqlite3.connect(database)
    self.assertEqual(connection.execute("select count(*) from positions").fetchone()[0], 2)
    connection.close()
