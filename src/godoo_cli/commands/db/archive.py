"""Thin wrappers around Odoo's database archive commands."""

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Optional

import typer

from ...cli_common import CommonCLI
from ...helpers.odoo_files import require_odoo_version
from .reset import odoo_db_command

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()

CommandRunner = Callable[[Sequence[str]], int]


def _run_odoo_db(command: Sequence[str]) -> int:
    """Run an Odoo database archive command without invoking a shell."""
    from ...helpers.odoo_command import run_odoo_command

    LOGGER.info("Running Odoo database command: %s", " ".join(command))
    return run_odoo_command(command).returncode


def dump_runtime_archive(
    *,
    db_name: str,
    archive_path: Path,
    odoo_bin_path: Path,
    odoo_conf_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    runner: CommandRunner = _run_odoo_db,
) -> int:
    """Create Odoo's ZIP archive for one database and its filestore."""
    return runner(
        odoo_db_command(
            odoo_bin_path=odoo_bin_path,
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
            arguments=["dump", db_name, str(archive_path)],
        )
    )


def load_runtime_archive(
    *,
    db_name: str,
    archive_path: Path,
    odoo_bin_path: Path,
    odoo_conf_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    force: bool = False,
    runner: CommandRunner = _run_odoo_db,
) -> int:
    """Load an Odoo ZIP archive, including only that archive's filestore."""
    arguments = ["load"]
    if force:
        arguments.append("--force")
    arguments.extend([db_name, str(archive_path)])
    return runner(
        odoo_db_command(
            odoo_bin_path=odoo_bin_path,
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
            arguments=arguments,
        )
    )


def dump_database(
    db_name: Annotated[str, CLI.database.db_name],
    archive_path: Annotated[
        Path,
        typer.Argument(help="Destination Odoo ZIP archive.", envvar="GODOO_ARCHIVE_PATH"),
    ],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Optional[Path], CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
) -> int:
    """Create an Odoo-native database and filestore archive."""
    require_odoo_version(odoo_main_path, ">=19")
    return CLI.returner(
        dump_runtime_archive(
            db_name=db_name,
            archive_path=archive_path,
            odoo_bin_path=odoo_main_path / "odoo-bin",
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
        )
    )


def load_database(
    db_name: Annotated[str, CLI.database.db_name],
    archive_path: Annotated[
        Path,
        typer.Argument(help="Source Odoo ZIP archive.", envvar="GODOO_ARCHIVE_PATH"),
    ],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Optional[Path], CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    force: Annotated[
        bool,
        typer.Option("--force", envvar="GODOO_DB_LOAD_FORCE", help="Replace an existing target database."),
    ] = False,
) -> int:
    """Load an Odoo-native database and filestore archive."""
    require_odoo_version(odoo_main_path, ">=19")
    return CLI.returner(
        load_runtime_archive(
            db_name=db_name,
            archive_path=archive_path,
            odoo_bin_path=odoo_main_path / "odoo-bin",
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
            force=force,
        )
    )
