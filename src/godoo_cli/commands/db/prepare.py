"""Typer adapter for database preparation."""

from pathlib import Path
from typing import Annotated

import typer

from ...runtime.prepare import prepare_runtime
from ..common import CommonCLI
from ..configuration import resolve_command_config

CLI = CommonCLI()


def prepare_database(
    db_name: Annotated[str, CLI.database.db_name],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    data_dir: Annotated[Path | None, CLI.odoo_paths.data_dir] = None,
    source_db: Annotated[str, typer.Option("--source-db")] = "",
    archive_path: Annotated[Path | None, typer.Option("--archive")] = None,
    filestore_path: Annotated[Path | None, typer.Option("--filestore")] = None,
    original_filestore: Annotated[
        Path | None,
        typer.Option("--original-filestore", envvar="GODOO_ORIGINAL_FILESTORE"),
    ] = None,
    strategy: Annotated[str, typer.Option("--strategy")] = "auto",
    force: Annotated[bool, typer.Option("--force")] = False,
    db_user: Annotated[str | None, CLI.database.db_user] = None,
    db_host: Annotated[str | None, CLI.database.db_host] = None,
    db_port: Annotated[int | None, CLI.database.db_port] = None,
    db_password: Annotated[str | None, CLI.database.db_password] = None,
) -> None:
    """Prepare a runtime with one selected safe strategy."""
    config = resolve_command_config(
        odoo_main_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        data_dir=data_dir,
        db_name=db_name,
        db_user=db_user,
        db_host=db_host,
        db_port=db_port,
        db_password=db_password,
    )
    try:
        selected = prepare_runtime(
            config,
            strategy=strategy,
            source_db=source_db,
            archive_path=archive_path,
            filestore_path=filestore_path,
            original_filestore=original_filestore,
            force=force,
        )
    except (ValueError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(selected.strategy.value)
