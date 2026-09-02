"""Filestore-aware runtime storage commands.

The commands in this module intentionally treat an Odoo database and its
filestore as one runtime.  Odoo's own ``db`` CLI continues to perform native
archive, load, duplicate, and drop operations.
"""

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Optional

import typer

from ...cli_common import CommonCLI
from ...helpers.odoo_files import require_odoo_version
from ..db.archive import dump_database, load_database
from ..db.cow import duplicate_cow
from ..db.reset import odoo_db_command, reset_empty_runtime

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()
CommandRunner = Callable[[Sequence[str]], int]


def clone_runtime(
    *,
    source: str,
    target: str,
    force: bool,
    odoo_bin_path: Path,
    odoo_conf_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    runner: Optional[CommandRunner] = None,
) -> int:
    """Clone one database and its filestore through Odoo's native CLI."""
    if source == target:
        LOGGER.error("Source and target database names must differ.")
        return 2
    command = odoo_db_command(
        odoo_bin_path=odoo_bin_path,
        odoo_conf_path=odoo_conf_path,
        data_dir=data_dir,
        arguments=["duplicate", *(["--force"] if force else []), source, target],
    )
    if runner is None:
        from ..db.reset import _run_odoo_db  # pylint: disable=import-outside-toplevel

        runner = _run_odoo_db
    return runner(command)


def clone_database(
    source: Annotated[str, typer.Argument(help="Source Odoo database.", envvar="GODOO_RUNTIME_SOURCE")],
    target: Annotated[str, typer.Argument(help="New Odoo database.", envvar="GODOO_RUNTIME_TARGET")],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    force: Annotated[
        bool,
        typer.Option("--force", envvar="GODOO_RUNTIME_CLONE_FORCE", help="Replace an existing target runtime."),
    ] = False,
    odoo_conf_path: Annotated[Optional[Path], CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
) -> int:
    """Clone a database and its matching filestore using ``odoo-bin db duplicate``."""
    require_odoo_version(odoo_main_path, ">=19")
    return CLI.returner(
        clone_runtime(
            source=source,
            target=target,
            force=force,
            odoo_bin_path=odoo_main_path / "odoo-bin",
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
        )
    )


def drop_runtime(
    db_name: Annotated[str, CLI.database.db_name],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Optional[Path], CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
) -> int:
    """Drop a database and its matching filestore through ``odoo-bin db drop``."""
    require_odoo_version(odoo_main_path, ">=19")
    return CLI.returner(
        reset_empty_runtime(
            db_name=db_name,
            odoo_bin_path=odoo_main_path / "odoo-bin",
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
        )
    )


def runtime_storage_cli_app() -> typer.Typer:
    """Return the storage command group for mounting below ``godoo runtime``."""
    app = typer.Typer(no_args_is_help=True, help="Manage database and filestore pairs as Odoo runtimes.")
    archive = typer.Typer(
        no_args_is_help=True, help="Create or load Odoo archives and legacy gOdoo 0.17 dump directories."
    )
    archive.command("create")(dump_database)
    archive.command("load")(load_database)
    app.add_typer(archive, name="archive")
    app.command("clone")(clone_database)
    app.command("clone-cow")(duplicate_cow)
    app.command("drop")(drop_runtime)
    return app
