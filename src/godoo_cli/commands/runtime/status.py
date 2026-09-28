"""Typer adapter for runtime status."""

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from ...database.connection import DBConnection
from ...runtime.status import inspect_runtime
from ..common import CommonCLI

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def runtime_status(
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    json_output: Annotated[bool, typer.Option("--json", help="Print structured runtime state.")] = False,
    provenance_path: Annotated[Path, typer.Option("--provenance-path", help="Production release metadata.")] = Path(
        "/odoo/godoo-source-provenance.json"
    ),
) -> None:
    """Inspect runtime state and release metadata without writes."""
    payload, exit_code = inspect_runtime(
        DBConnection(db_host, db_port, db_user, db_password, db_name, readonly=True), data_dir, provenance_path
    )
    typer.echo(
        json.dumps(payload, sort_keys=True)
        if json_output
        else f"Runtime '{db_name}': {payload['state']}. {payload['reason']}"
    )
    raise typer.Exit(exit_code)
