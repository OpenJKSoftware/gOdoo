"""Odoo-managed database reset commands."""

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Optional

from ...cli_common import CommonCLI
from ...helpers.odoo_command import run_odoo_command
from ...helpers.odoo_files import require_odoo_version

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()

CommandRunner = Callable[[Sequence[str]], int]


def _run_odoo_db(command: Sequence[str]) -> int:
    """Run an Odoo database command without invoking a shell."""
    LOGGER.info("Running Odoo database command: %s", " ".join(command))
    return run_odoo_command(command).returncode


def odoo_db_command(
    *,
    odoo_bin_path: Path,
    odoo_conf_path: Optional[Path],
    data_dir: Optional[Path],
    arguments: Sequence[str],
) -> list[str]:
    """Build an Odoo 19 filestore-aware database command."""
    command = [str(odoo_bin_path), "db"]
    if odoo_conf_path is not None:
        command.extend(["--config", str(odoo_conf_path)])
    if data_dir is not None:
        command.extend(["--data-dir", str(data_dir)])
    command.extend(arguments)
    return command


def reset_runtime_from_template(
    *,
    db_name: str,
    db_template_name: str,
    odoo_bin_path: Path,
    odoo_conf_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    runner: CommandRunner = _run_odoo_db,
    **_: object,
) -> int:
    """Replace a database and filestore through ``odoo-bin db duplicate``."""
    if db_name == db_template_name:
        LOGGER.error("Template and target database names must differ.")
        return 2
    command = odoo_db_command(
        odoo_bin_path=odoo_bin_path,
        odoo_conf_path=odoo_conf_path,
        data_dir=data_dir,
        arguments=["duplicate", "--force", db_template_name, db_name],
    )
    return runner(command)


def reset_empty_runtime(
    *,
    db_name: str,
    odoo_bin_path: Path,
    odoo_conf_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    runner: CommandRunner = _run_odoo_db,
    **_: object,
) -> int:
    """Drop a database and its filestore through ``odoo-bin db drop``."""
    command = odoo_db_command(
        odoo_bin_path=odoo_bin_path,
        odoo_conf_path=odoo_conf_path,
        data_dir=data_dir,
        arguments=["drop", db_name],
    )
    return runner(command)


def reset_database_from_template(
    db_name: Annotated[str, CLI.database.db_name],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Optional[Path], CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    db_template_name: Annotated[str, CLI.database.db_template_name] = "",
) -> int:
    """Replace a database and filestore from an Odoo database template."""
    require_odoo_version(odoo_main_path, ">=19")
    return CLI.returner(
        reset_runtime_from_template(
            db_name=db_name,
            db_template_name=db_template_name or f"{db_name}_template",
            odoo_bin_path=odoo_main_path / "odoo-bin",
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
        )
    )


def reset_odoo_state(
    db_name: Annotated[str, CLI.database.db_name],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Optional[Path], CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    db_template_name: Annotated[str, CLI.database.db_template_name] = "",
    empty_reset: Annotated[bool, CLI.database.empty_reset] = False,
) -> int:
    """Drop a runtime database or replace it from its explicit template."""
    require_odoo_version(odoo_main_path, ">=19")
    if empty_reset:
        return CLI.returner(
            reset_empty_runtime(
                db_name=db_name,
                odoo_bin_path=odoo_main_path / "odoo-bin",
                odoo_conf_path=odoo_conf_path,
                data_dir=data_dir,
            )
        )
    return reset_database_from_template(
        db_name=db_name,
        odoo_main_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        data_dir=data_dir,
        db_template_name=db_template_name,
    )
