"""Command-line entry point for importing the configured report."""

from __future__ import annotations

import argparse

from .config import load_config
from .service import ImportService


def main() -> None:
    """Import the configured workbook into the SQLite projection."""
    parser = argparse.ArgumentParser(description="Reconstrói a projeção SQLite do algobotdash")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--database", default="data/algobotdash.sqlite")
    args = parser.parse_args()
    summary = ImportService(load_config(args.config)).refresh(args.database)
    print(
        f"Importação válida: {summary.positions_created} posições, "
        f"{summary.rejected_count} rejeições"
    )


if __name__ == "__main__":
    main()
