"""Prepare and launch Odoo processes."""

import logging
from pathlib import Path
from typing import Annotated

import typer

from ...runtime.odoo import (
    _extra_args_argv,
    build_launch_command,
    execution_python,
    odoo_debugger_attached,
    preflight_for_config,
    run_odoo_command,
)
from ..common import CommonCLI
from ..configuration import require_cli_odoo_version, resolve_command_config

CLI = CommonCLI()

LOGGER = logging.getLogger(__name__)


def launch_odoo(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_filter: Annotated[str | None, CLI.database.db_filter] = None,
    db_name: Annotated[str | None, CLI.database.db_name] = None,
    db_user: Annotated[str | None, CLI.database.db_user] = None,
    data_dir: Annotated[Path | None, CLI.odoo_paths.data_dir] = None,
    db_host: Annotated[str | None, CLI.database.db_host] = None,
    db_password: Annotated[str | None, CLI.database.db_password] = None,
    db_port: Annotated[int | None, CLI.database.db_port] = None,
    extra_args: Annotated[list[str] | None, CLI.odoo_launch.extra_cmd_args] = None,
    log_file_path: Annotated[Path | None, CLI.odoo_launch.log_file_path] = None,
    dev_mode: Annotated[bool, CLI.odoo_launch.dev_mode] = False,
    multithread_worker_count: Annotated[int, CLI.odoo_launch.multithread_worker_count] = 2,
    languages: Annotated[str, CLI.odoo_launch.languages] = "de_DE,en_US",
    debug_listen: Annotated[
        str | None,
        typer.Option(
            "--debug-listen",
            help="Run Odoo through debugpy and listen on HOST:PORT.",
        ),
    ] = None,
    debug_wait_for_client: Annotated[
        bool,
        typer.Option(
            "--debug-wait-for-client",
            help="Wait for a debugger before executing Odoo (requires --debug-listen).",
        ),
    ] = False,
):
    """Launch Odoo without initialization or reconciliation, after additive dependency preflight."""
    parent_debugger = odoo_debugger_attached()
    launch_args = _extra_args_argv(extra_args or [])
    godoo_conf = resolve_command_config(
        odoo_main_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        data_dir=data_dir,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_filter=db_filter,
        arguments=launch_args,
        extra={"multithread_worker_count": multithread_worker_count, "languages": languages},
    )
    require_cli_odoo_version(godoo_conf.odoo_install_folder, ">=16,<20")
    if log_file_path is not None:
        log_file_path.unlink(missing_ok=True)
        launch_args.extend(["--logfile", str(log_file_path.absolute())])
    if debug_wait_for_client and not debug_listen and not parent_debugger:
        message = "--debug-wait-for-client requires --debug-listen."
        raise typer.BadParameter(message)
    if dev_mode:
        launch_args.extend(["--dev", "xml,qweb" if debug_listen or parent_debugger else "xml,qweb,reload"])
    preflight_for_config(godoo_conf, launch_args)
    launch_cmd = build_launch_command(godoo_conf, launch_args, upgrade_workspace_modules=False)
    if debug_listen and not parent_debugger:
        launch_cmd = [
            str(execution_python()),
            "-m",
            "debugpy",
            "--listen",
            debug_listen,
            *(["--wait-for-client"] if debug_wait_for_client else []),
            *launch_cmd,
        ]

    LOGGER.info("Launching Odoo on database '%s' using config %s", db_name, odoo_conf_path)
    return CLI.returner(run_odoo_command(launch_cmd).returncode)
