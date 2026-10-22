"""Run the official Odoo upgrade client with passthrough arguments."""

from __future__ import annotations

import logging
import os
import signal
import ssl
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Annotated, cast
from urllib.error import URLError
from urllib.request import urlopen

import typer
from rich import print as rich_print

from ..helpers.progress import ProgressPhase

LOGGER = logging.getLogger(__name__)
UPGRADE_CLIENT_URL = "https://upgrade.odoo.com/upgrade"


def _run_upgrade_client(arguments: list[str]) -> int:
    """Download and run Odoo's official upgrade client with its arguments."""
    ssl_disabled = os.getenv("GODOO_UPGRADE_SSL") == "0"
    context = ssl._create_unverified_context() if ssl_disabled else None
    try:
        with (
            ProgressPhase("Downloading official Odoo upgrade client", logger=LOGGER),
            urlopen(UPGRADE_CLIENT_URL, timeout=60, context=context) as response,
        ):
            client = response.read()
    except (OSError, URLError):
        LOGGER.exception("Could not download the official Odoo upgrade client.")
        rich_print("[red]Could not download the official Odoo upgrade client.[/red]")
        return 1

    with tempfile.TemporaryDirectory(prefix="godoo-upgrade-") as directory:
        client_path = Path(directory) / "upgrade.py"
        client_path.write_bytes(client)
        child_environment = os.environ.copy()
        child_environment["SSL_VERIFICATION"] = "0" if ssl_disabled else "1"
        return _run_process([sys.executable, str(client_path), *arguments], env=child_environment)


def _run_process(command: list[str], *, env: dict[str, str] | None = None) -> int:
    """Run the client while forwarding stop signals to its process group."""
    process = subprocess.Popen(command, start_new_session=True, env=env)
    previous_handlers: dict[int, signal.Handlers] = {}

    def forward(signum: int, _frame: object) -> None:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = cast(signal.Handlers, signal.signal(signum, forward))
        with ProgressPhase("Running official Odoo upgrade client", heartbeat=False, logger=LOGGER) as progress:
            returncode = process.wait()
            progress.failed = returncode != 0
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    if returncode < 0:
        signum = -returncode
        os.kill(os.getpid(), signum)
        return 128 + signum
    return returncode


def upgrade(
    arguments: Annotated[list[str], typer.Argument(help="Arguments for Odoo's official upgrade client.")],
) -> None:
    """Run the official Odoo database upgrade client."""
    raise typer.Exit(_run_upgrade_client(arguments))
