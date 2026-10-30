"""Prepare and launch Odoo processes."""

import logging
import os
from pathlib import Path
from typing import Annotated

import typer

from ...models import GodooConfig
from ...runtime.lifecycle import run_lifecycle_hook
from ...runtime.nginx import write_nginx_static_config
from ...runtime.odoo import (
    SUPPORTED_ODOO_VERSION_SPECIFIER,
    _extra_args_argv,
    build_launch_command,
    execution_python,
    odoo_debugger_attached,
    preflight_for_config,
    require_runtime_launch_ready,
    resolve_odoo_config,
    run_odoo_command,
)
from ..common import CommonCLI
from ..configuration import require_cli_odoo_version, resolve_command_config
from .shell import SHELL_SCRIPTS_PATH

CLI = CommonCLI()

LOGGER = logging.getLogger(__name__)


# Typer declares each server and debugger option in this public callback signature.
# pylint: disable-next=too-many-arguments
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
    nginx_config: Annotated[Path | None, CLI.odoo_paths.nginx_config] = None,
):
    """Launch Odoo without initialization or reconciliation, after additive dependency preflight."""
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
    return CLI.returner(
        launch_prepared_runtime(
            godoo_conf,
            extra_args=extra_args,
            log_file_path=log_file_path,
            dev_mode=dev_mode,
            debug_listen=debug_listen,
            debug_wait_for_client=debug_wait_for_client,
            nginx_config=nginx_config,
        )
    )


def launch_prepared_runtime(
    godoo_conf: GodooConfig,
    *,
    extra_args: list[str] | None = None,
    log_file_path: Path | None = None,
    dev_mode: bool = False,
    debug_listen: str | None = None,
    debug_wait_for_client: bool = False,
    nginx_config: Path | None = None,
):
    """Launch an already-resolved runtime after readiness and dependency checks."""
    parent_debugger = odoo_debugger_attached()
    launch_args = _extra_args_argv(extra_args or [])
    runtime_version = require_cli_odoo_version(godoo_conf.odoo_install_folder, SUPPORTED_ODOO_VERSION_SPECIFIER)
    if log_file_path is not None:
        log_file_path.unlink(missing_ok=True)
        launch_args.extend(["--logfile", str(log_file_path.absolute())])
    if debug_wait_for_client and not debug_listen and not parent_debugger:
        message = "--debug-wait-for-client requires --debug-listen."
        raise typer.BadParameter(message)
    if dev_mode:
        launch_args.extend(["--dev", "xml,qweb" if debug_listen or parent_debugger else "xml,qweb,reload"])
    require_runtime_launch_ready(godoo_conf)
    preflight_for_config(godoo_conf, launch_args)
    launch_cmd = build_launch_command(godoo_conf, launch_args, upgrade_workspace_modules=False)
    if nginx_config is not None:
        write_nginx_static_config(resolve_odoo_config(godoo_conf, launch_cmd[1:]), nginx_config, runtime_version)
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

    if any(os.environ.get(name, "").strip() for name in ("ODOO_BANNER_TEXT", "ODOO_BANNER_BG_COLOR")):
        LOGGER.info("Applying the configured Odoo banner")
        result = run_lifecycle_hook(godoo_conf, SHELL_SCRIPTS_PATH / "odoo_banner.py")
        if result:
            LOGGER.error("Odoo banner update failed (exit code %s)", result)
            return result

    LOGGER.info("Launching Odoo on database '%s' using config %s", godoo_conf.db_name, godoo_conf.odoo_conf_path)
    return run_odoo_command(launch_cmd).returncode
