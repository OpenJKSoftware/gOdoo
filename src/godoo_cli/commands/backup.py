"""Deprecated backup command compatibility surface."""

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer

from ..cli_common import CommonCLI
from .db.archive import dump_database

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def dump_legacy_backup(
    dump_path: Annotated[
        Path,
        typer.Argument(
            file_okay=False,
            dir_okay=True,
            writable=True,
            resolve_path=True,
            envvar="GODOO_DUMP_PATH",
            help="Directory receiving runtime.zip.",
        ),
    ],
    db_name: Annotated[str, CLI.database.db_name],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Optional[Path], CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
) -> int:
    """Create the native atomic archive through the legacy backup command."""
    LOGGER.warning("godoo backup dump is deprecated; use godoo runtime storage archive create /path/runtime.zip.")
    return dump_database(
        db_name=db_name,
        archive_path=dump_path / "runtime.zip",
        odoo_main_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        data_dir=data_dir,
    )


def backup_cli_app() -> typer.Typer:
    """Return the deprecated backup command group."""
    app = typer.Typer(no_args_is_help=True, help="Deprecated compatibility aliases for runtime storage archives.")
    app.command("dump")(dump_legacy_backup)
    return app
