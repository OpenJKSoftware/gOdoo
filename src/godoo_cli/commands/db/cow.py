"""Typer adapter for copy-on-write cloning."""

from pathlib import Path
from typing import Annotated

import typer

from ...runtime.cow import duplicate_cow_runtime
from ..common import CommonCLI

CLI = CommonCLI()


def duplicate_cow(
    source: Annotated[str, typer.Argument()],
    target: Annotated[str, typer.Argument()],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    db_user: Annotated[str, CLI.database.db_user],
    force: Annotated[bool, typer.Option("--force", envvar="ODOO_DB_DUPLICATE_COW_FORCE")] = False,
    odoo_conf_path: Annotated[Path | None, CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
) -> int:
    """Clone a database and filestore through strict CoW."""
    return CLI.returner(
        duplicate_cow_runtime(
            source=source,
            target=target,
            force=force,
            odoo_main_path=odoo_main_path,
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
        )
    )
