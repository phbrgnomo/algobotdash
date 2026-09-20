"""Command-line entry point for importing the configured report."""

from __future__ import annotations

import argparse
import os
from typing import cast

from .config import load_config
from .environment import load_environment
from .refresh import (
    DatabaseRefreshLock,
    RefreshInProgressError,
    RefreshLockUnavailableError,
    RefreshOperationStore,
)
from .service import ImportService


def main() -> None:
    """Import the configured workbook into the SQLite projection."""
    _ = load_environment()
    parser = argparse.ArgumentParser(description="Reconstrói a projeção SQLite do algobotdash")
    _ = parser.add_argument(
        "--config",
        default=os.getenv("ALGOBOTDASH_CONFIG", "config/config.yaml"),
    )
    _ = parser.add_argument(
        "--database",
        default=os.getenv("ALGOBOTDASH_DATABASE", "data/algobotdash.sqlite"),
    )
    args = parser.parse_args()
    config_path = cast(str, args.config)
    database_path = cast(str, args.database)
    store = RefreshOperationStore.for_database(database_path)
    try:
        with DatabaseRefreshLock(database_path):
            store.reconcile_interrupted(database_path, live_operation_ids=set())
            summary = ImportService(load_config(config_path)).refresh_with_lock(database_path)
    except RefreshInProgressError as exc:
        raise SystemExit("Já existe uma atualização em andamento para este banco.") from exc
    except RefreshLockUnavailableError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"Importação válida: {summary.positions_created} posições, "
        f"{summary.rejected_count} rejeições"
    )


if __name__ == "__main__":
    main()
