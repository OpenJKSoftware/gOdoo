"""Run arbitrary ``odoo-bin`` arguments through gOdoo's execution contract."""

import logging
from pathlib import Path
from typing import Annotated

import typer

from ..runtime.odoo import (
    odoo_database_args,
    preflight_for_config,
    require_runtime_database_major,
    require_runtime_launch_ready,
    run_odoo_command,
)
from .common import CommonCLI
from .configuration import resolve_command_config

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def _odoo_command_name(arguments: list[str]) -> str | None:
    """Return the first native Odoo command without parsing its arguments."""
    if not arguments or arguments[0] == "--":
        return None
    first = arguments[0]
    if first.startswith(("--addons-path=", "--addons_path=")):
        second = arguments[1] if len(arguments) > 1 else None
        return second if second and not second.startswith("-") else None
    return None if first.startswith("-") else first


def _uses_managed_server_config(arguments: list[str]) -> bool:
    """Keep implicit and explicit server invocations on the managed path."""
    return _odoo_command_name(arguments) in {None, "server"}


def run_odoo(
    arguments: Annotated[list[str], typer.Argument(help="Arguments to pass directly to odoo-bin.")],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    data_dir: Annotated[Path | None, CLI.odoo_paths.data_dir] = None,
    db_name: Annotated[str | None, CLI.database.db_name] = None,
    db_user: Annotated[str | None, CLI.database.db_user] = None,
    db_host: Annotated[str | None, CLI.database.db_host] = None,
    db_port: Annotated[int | None, CLI.database.db_port] = None,
    db_password: Annotated[str | None, CLI.database.db_password] = None,
) -> int:
    """Run any Odoo command after the shared additive dependency preflight."""
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
        arguments=arguments,
    )
    if not _uses_managed_server_config(arguments):
        if _odoo_command_name(arguments) == "shell":
            require_runtime_database_major(config)
        preflight_for_config(config, arguments, include_module_dependencies=False)
        return CLI.returner(run_odoo_command([str(config.odoo_bin_path), *arguments]).returncode)

    require_runtime_launch_ready(config)
    preflight_for_config(config, arguments)
    child_args = [
        "--config",
        str(config.odoo_conf_path.absolute()),
        "--data-dir",
        str(config.data_dir.absolute()),
        *odoo_database_args(
            db_name=config.db_name,
            db_user=config.db_user,
            db_password=config.db_password,
            db_host=config.db_host,
            db_port=config.db_port,
            db_sslmode=config.db_sslmode,
        ),
    ]
    separator = arguments.index("--") if "--" in arguments else len(arguments)
    command = [str(config.odoo_bin_path), *arguments[:separator], *child_args, *arguments[separator:]]
    return CLI.returner(run_odoo_command(command).returncode)
