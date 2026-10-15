"""Run the chart smoke against an isolated, disposable HTTP server.

Requires Node.js and Playwright with Chromium. Set PLAYWRIGHT_MODULE when the
Node package is installed outside the default module search path.
"""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import subprocess  # nosec B404 -- fixed local test commands
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from algobotdash.config import load_config
from algobotdash.service import ImportService
from tests.fixture_helpers import insert_positions, workbook


def main() -> None:
    """Seed public fixtures, verify desktop/mobile, and stop the temporary server."""
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js is required")
    with tempfile.TemporaryDirectory(prefix="algobotdash-browser-") as directory:
        root = Path(directory)
        source = root / "history.xlsx"
        workbook(source, legacy_report=True)
        config = root / "config.yaml"
        config.write_text(
            f"timezone: America/Bahia\nsource:\n  path: {source}\n"
            "symbols:\n  prefixes:\n    WIN: WIN\n    WDO: WDO\n"
            "strategies:\n  groups:\n    - name: FVG\n      patterns: [fvg]\n",
            encoding="utf-8",
        )
        database = root / "projection.sqlite"
        ImportService(load_config(config)).refresh(database)
        with closing(sqlite3.connect(database)) as connection:
            insert_positions(connection, [
                ("smoke", "Turtle", "WIN", "WINQ26", "buy",
                 "2026-08-01T12:00:00Z", "2026-08-03T15:00:00Z", "closed",
                 1, 1, 100, 95, 0, 0, -30, 1, 1),
                ("residual", None, "WDO", "WDOQ26", "sell",
                 "2026-08-01T12:00:00Z", "2026-08-04T15:00:00Z", "closed",
                 1, 1, 100, 105, 0, 0, 7, 0, 1),
            ])
            connection.commit()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        environment = {
            **os.environ, "ALGOBOTDASH_CONFIG": str(config),
            "ALGOBOTDASH_DATABASE": str(database), "SMOKE_URL": f"http://127.0.0.1:{port}",
        }
        workaround = (
            'import sys; sys.modules["_asyncio"] = None; '
            if os.getenv("ALGOBOTDASH_PURE_ASYNCIO") == "1" else ""
        )
        command = workaround + (
            f'import uvicorn; uvicorn.run("algobotdash.web:app", host="127.0.0.1", '
            f'port={port}, log_level="warning")'
        )
        with (root / "server.log").open("w", encoding="utf-8") as log:
            with subprocess.Popen(  # nosec B603 -- fixed Python entrypoint, fixture paths
                [sys.executable, "-c", command], env=environment, stdout=log, stderr=log
            ) as server:
                try:
                    subprocess.run(  # nosec B603 -- fixed smoke script, absolute Node path
                        [node, str(Path(__file__).with_suffix(".cjs"))], env=environment,
                        check=True, timeout=60,
                    )
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
                    if server.returncode not in (0, -15):
                        print((root / "server.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
