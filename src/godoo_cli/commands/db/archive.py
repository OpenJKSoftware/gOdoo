"""Typer adapters for runtime archive operations."""

import logging
from pathlib import Path
from typing import Annotated

import psycopg2
import typer

from ...database.connection import DBConnection
from ...runtime.archive import (
    RuntimeRestoreError,
    dump_runtime_archive,
    load_legacy_runtime_dump,
    load_runtime_archive,
)
from ..common import CommonCLI
from ..configuration import require_cli_odoo_version, resolve_development_odoo_main_path

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def dump_database(
    db_name: Annotated[str, CLI.database.db_name],
    archive_path: Annotated[Path, typer.Argument()],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Path | None, CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
) -> int:
    """Create an Odoo runtime archive."""
    try:
        odoo_main_path = resolve_development_odoo_main_path(odoo_main_path)
        version = require_cli_odoo_version(odoo_main_path, ">=16,<20")
        return CLI.returner(
            dump_runtime_archive(
                db_name=db_name,
                archive_path=archive_path,
                odoo_bin_path=odoo_main_path / "odoo-bin",
                odoo_conf_path=odoo_conf_path,
                data_dir=data_dir,
                odoo_version=version.major,
                connection=DBConnection.from_odoo_config(db_name, odoo_conf_path),
            )
        )
    except OSError:
        LOGGER.exception("Archive creation failed")
        return CLI.returner(1)


def load_database(
    db_name: Annotated[str, CLI.database.db_name],
    archive_path: Annotated[Path, typer.Argument()],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Path | None, CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    db_user: Annotated[str | None, CLI.database.db_user] = None,
    db_host: Annotated[str | None, CLI.database.db_host] = None,
    db_port: Annotated[int | None, CLI.database.db_port] = None,
    db_password: Annotated[str | None, CLI.database.db_password] = None,
    force: Annotated[bool, typer.Option("--force", envvar="GODOO_DB_LOAD_FORCE")] = False,
    db_template: Annotated[str, CLI.database.db_template_name] = "template0",
) -> int:
    """Load an archive through staged promotion."""
    try:
        connection = DBConnection.from_odoo_config(db_name, odoo_conf_path).with_overrides(
            hostname=db_host,
            port=db_port,
            username=db_user,
            password=db_password,
        )
        if archive_path.is_dir():
            if not force:
                return CLI.returner(2)
            load_legacy_runtime_dump(
                db_name=db_name,
                source_folder=archive_path,
                data_dir=data_dir,
                db_template=db_template,
                connection=connection,
                force=force,
            )
            return CLI.returner(0)
        odoo_main_path = resolve_development_odoo_main_path(odoo_main_path)
        version = require_cli_odoo_version(odoo_main_path, ">=16,<20")
        return CLI.returner(
            load_runtime_archive(
                db_name=db_name,
                archive_path=archive_path,
                odoo_bin_path=odoo_main_path / "odoo-bin",
                odoo_conf_path=odoo_conf_path,
                data_dir=data_dir,
                force=force,
                odoo_version=version.major,
                connection=connection,
            )
        )
    except (RuntimeRestoreError, OSError, ValueError, psycopg2.Error):
        LOGGER.exception("Archive load failed")
        return CLI.returner(1)
