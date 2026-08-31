"""Unit and integration tests for the legacy static report generator."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

import generate_trade_report as report


class LegacyReportTests(unittest.TestCase):
    """Verify report metrics, escaping, and file generation."""

    def test_metrics_handle_empty_and_edge_samples(self) -> None:
        """Metrics should remain defined for empty, winning, and losing samples."""
        self.assertEqual(report.m([])["n"], 0)
        self.assertEqual(report.m([1.0, 2.0])["pf"], 99)
        self.assertEqual(report.m([-1.0, -2.0])["pf"], 0)
        self.assertIsNone(report.m([1.0] * 5)["sh"])
        self.assertIsNone(report.bs([1.0] * 9))

    def test_parsers_handle_malformed_values(self) -> None:
        """Date and number parsers should reject malformed input safely."""
        self.assertIsNone(report.date("not-a-date"))
        self.assertIsNone(report.num("not-a-number"))
        self.assertIsNone(report.num("nan"))
        self.assertIsNone(report.num("inf"))

    def test_svg_escapes_series_names(self) -> None:
        """SVG legends should escape user-derived series names."""
        page = report.svg_multi({"<WIN>": [1.0, 2.0]}, "P&L")

        self.assertIn("&lt;WIN&gt;", page)
        self.assertNotIn("<WIN>", page)

    def test_main_writes_report_from_configured_source(self) -> None:
        """The generator should read the workbook path from the YAML config."""
        with tempfile.TemporaryDirectory(prefix="algobotdash-report-tests-") as raw_dir:
            directory = Path(raw_dir)
            source = directory / "ReportHistory.xlsx"
            config_path = directory / "config.yaml"
            output = directory / "reports"
            self._write_workbook(source)
            config_path.write_text(
                f"source:\n  path: {source}\n",
                encoding="utf-8",
            )

            with patch.object(report, "CONFIG_PATH", config_path), patch.object(
                report, "OUT", output
            ):
                report.main()

            generated = output / "avaliacao_estrategias_2026-08-28.html"
            self.assertTrue(generated.is_file())
            self.assertIn(
                "Avaliação Quantitativa de Estratégias",
                generated.read_text(encoding="utf-8"),
            )

    @staticmethod
    def _write_workbook(path: Path) -> None:
        """Write the smallest valid workbook accepted by the legacy generator."""
        book = Workbook()
        sheet = book.active
        if sheet is None:
            raise RuntimeError("workbook fixture has no active worksheet")
        sheet.append(("Posições",))
        sheet.append(
            (
                "Horário", "Position", "Ativo", "Tipo", "Volume", "Preço",
                "S / L", "T / P", "Horário", "Preço", "Comissão", "Swap",
                "Lucro",
            )
        )
        sheet.append(
            (
                "2026.08.01 10:00:00", 1001, "WINQ26", "buy", 1, 130000,
                None, None, None, None, 0, 0, 100,
            )
        )
        sheet.append((None, None, None, None))
        sheet.append(("Ordens",))
        sheet.append(
            (
                "Horário da Abertura", "Ordem", "Ativo", "Tipo", "Volume",
                "Preço", "S / L", "T / P", "Horário", "Estado", None,
                "Comentário",
            )
        )
        sheet.append(
            (
                "2026.08.01 09:59:00", 1001, "WINQ26", "buy", 1, 130000,
                None, None, None, "filled", None, "FVGscalp",
            )
        )
        sheet.append(("Transações",))
        sheet.append(
            (
                "Horário", "Oferta", "Ativo", "Tipo", "Direção", "Volume",
                "Preço", "Ordem", "Comissão", "Taxa", "Swap", "Lucro",
                "Saldo", "Comentário",
            )
        )
        book.save(path)
