"""Unit and integration tests for the legacy static report generator."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generate_trade_report as report
from tests.fixture_helpers import workbook


class LegacyReportTests(unittest.TestCase):
    """Verify report metrics, escaping, and file generation."""

    def test_default_config_path_matches_application_convention(self) -> None:
        """The legacy generator should use the documented config location."""
        self.assertEqual(report.CONFIG_PATH, Path("config/config.yaml"))

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
            workbook(source, legacy_report=True)
            config_path.write_text(
                f"source:\n  path: {source}\n",
                encoding="utf-8",
            )

            with patch.object(report, "CONFIG_PATH", config_path), patch.object(
                report, "OUT", output
            ):
                report.main()

            generated = output / "avaliacao_estrategias_2026-08-01.html"
            self.assertTrue(generated.is_file())
            self.assertIn(
                "Avaliação Quantitativa de Estratégias",
                generated.read_text(encoding="utf-8"),
            )
