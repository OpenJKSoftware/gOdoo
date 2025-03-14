"""Database restore functionality for Odoo instances.

This module provides functionality for loading database dumps into Odoo instances,
supporting various dump formats and handling database creation and restoration.
"""

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer

from ...cli_common import CommonCLI
from ..db.connection import DBConnection
from .util import call_rsync, create_db, drop_db

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def load_pg_dump(
    dump_path: Path,
    db_name: str,
    db_host: Optional[str] = None,
    db_port: int = 0,
    db_user: Optional[str] = None,
    db_password: Optional[str] = None,
):
    """Drop and recreate db_name and restore dump_path into it."""
    if not dump_path.exists():
        msg = f"Cannot find Odoo Dump @ {dump_path}"
        LOGGER.error(msg)
        raise FileNotFoundError(msg)

    conn = DBConnection(
        db_name="postgres",
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
    )

    drop_db(conn, db_name)
    create_db(conn, db_name)

    conn.db_name = db_name

    LOGGER.info(
        "Loading DB Dump: %s [%s mb] --> %s",
        dump_path,
        round(dump_path.stat().st_size / (1024 * 1024), 2),
        db_name,
    )
    command = (
        f"pg_restore --no-owner --format c --dbname {db_name} --jobs 8 {dump_path}"
        if dump_path.suffix == ".dump"
        else f"cat {dump_path} | psql {db_name} >/dev/null"
    )
    load_return = conn.run_psql_shell_command(
        command=command,
        text=True,
        capture_output=True,
    )
    if load_return.returncode != 0:
        LOGGER.error("Failed to load DB Dump: %s", load_return.stderr)
        raise typer.Exit(1)
    LOGGER.info("Deleting RPC Import Cache using plain SQL")
    with conn.connect() as cur:
        cur.execute("DELETE FROM ir_config_parameter WHERE key='godoo_rpc_import_cache';")
    return load_return.returncode


def load_instance_data(
    source_folder: Annotated[
        Path,
        typer.Argument(
            help="Source Folder to load data from.",
            file_okay=False,
            dir_okay=True,
            exists=True,
            envvar="GODOO_DUMP_PATH",
        ),
    ],
    filestore_target_folder: Annotated[
        Path,
        typer.Argument(
            help="Target Folder to load filestore to.",
            file_okay=False,
            dir_okay=True,
        ),
    ],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    dump_file_name: Annotated[
        str,
        typer.Option(help="Optional Changeable filename of Odoo SQL Dump within source_folder"),
    ] = "odoo.dump",
):
    """Copy Filestore cache and load SQL Dump into Postgres for gOdoo."""
    load_pg_dump(
        dump_path=source_folder / dump_file_name,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
    )
    LOGGER.info("Done Loading Odoo instance from %s", source_folder)

    filestore_target_folder.mkdir(parents=True, exist_ok=True)
    call_rsync(
        source_folder=source_folder / "odoo_filestore",
        target_folder=filestore_target_folder,
    )
