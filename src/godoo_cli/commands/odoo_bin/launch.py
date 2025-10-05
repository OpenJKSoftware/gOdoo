"""Odoo instance launch and management module.

This module provides functionality for launching and managing Odoo instances,
including bootstrapping new databases, handling configuration, and managing
the launch process with various options like development mode and worker counts.
"""

import logging
import re
import threading
from pathlib import Path
from typing import Annotated, Optional

import typer

from ...cli_common import CommonCLI
from ...helpers.system import run_cmd
from ...models import GodooConfig
from ..rpc import import_to_odoo
from .bootstrap import bootstrap_and_prep_launch_cmd

CLI = CommonCLI()

LOGGER = logging.getLogger(__name__)


def launch_odoo(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_filter: Annotated[str, CLI.database.db_filter],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_password: Annotated[str, CLI.database.db_password] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    extra_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args] = None,
    extra_bootstrap_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
    log_file_path: Annotated[Optional[Path], CLI.odoo_launch.log_file_path] = None,
    install_workspace_modules: Annotated[bool, CLI.odoo_launch.install_workspace_modules] = True,
    odoo_demo: Annotated[bool, CLI.odoo_launch.odoo_demo] = False,
    dev_mode: Annotated[bool, CLI.odoo_launch.dev_mode] = False,
    multithread_worker_count: Annotated[int, CLI.odoo_launch.multithread_worker_count] = 2,
    languages: Annotated[str, CLI.odoo_launch.languages] = "de_DE,en_US",
):
    """Launch an Odoo instance, bootstrapping if necessary.

    This command handles the complete process of launching an Odoo instance:
    1. Creates a new database if it doesn't exist
    2. Bootstraps the database with initial data if needed
    3. Configures the instance with specified options
    4. Launches Odoo with the appropriate configuration

    The function uses CLI class defaults for most parameters, which can be
    overridden through command line arguments or environment variables.

    Returns:
        int: 0 for success, non-zero for failure.
    """
    godoo_conf = GodooConfig(
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_filter=db_filter,
        odoo_install_folder=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        multithread_worker_count=multithread_worker_count,
        languages=languages,
    )
    launch_cmd = bootstrap_and_prep_launch_cmd(
        godoo_conf=godoo_conf,
        odoo_demo=odoo_demo,
        dev_mode=dev_mode,
        install_workspace_addons=install_workspace_modules,
        extra_launch_args=extra_args,
        extra_bootstrap_args=extra_bootstrap_args,
        log_file_path=log_file_path,
    )

    if not isinstance(launch_cmd, str):
        LOGGER.error("godoo Launch Failed. Bootstrap unsuccessfull. Aborting Launch...")
        return CLI.returner(launch_cmd)

    LOGGER.info("Launching Odoo")
    return CLI.returner(run_cmd(launch_cmd).returncode)


def launch_import(
    load_data_path: Annotated[
        list[Path],
        typer.Argument(help="Starts Async Importer Job with provided path(s)."),
    ],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_filter: Annotated[str, CLI.database.db_filter],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    rpc_host: Annotated[str, CLI.rpc.rpc_host],
    rpc_user: Annotated[str, CLI.rpc.rpc_user],
    rpc_password: Annotated[str, CLI.rpc.rpc_password],
    odoo_demo: Annotated[bool, CLI.odoo_launch.odoo_demo],
    dev_mode: Annotated[bool, CLI.odoo_launch.dev_mode],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    extra_launch_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args] = None,
    extra_bootstrap_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
    log_file_path: Annotated[Optional[Path], CLI.odoo_launch.log_file_path] = None,
    install_workspace_modules: Annotated[bool, CLI.odoo_launch.install_workspace_modules] = True,
    multithread_worker_count: Annotated[int, CLI.odoo_launch.multithread_worker_count] = 2,
):
    """Launch Odoo and import data from specified paths.

    This command launches an Odoo instance and starts a separate thread to import
    data through RPC. The import process runs asynchronously while Odoo is running.

    Args:
        load_data_path: List of paths containing data to import.
        odoo_main_path: Path to the Odoo installation directory.
        workspace_addon_path: Path to workspace addons directory.
        thirdparty_addon_path: Path to thirdparty addons directory.
        odoo_conf_path: Path to odoo.conf file.
        db_filter: Database filter pattern for odoo.conf.
        db_host: Database host address.
        db_port: Database port number.
        db_name: Name of the database to use.
        db_user: Database user name.
        db_password: Database password.
        rpc_host: Host address for RPC connections.
        rpc_user: Username for RPC authentication.
        rpc_password: Password for RPC authentication.
        odoo_demo: If True, load demo data during bootstrap.
        dev_mode: If True, enable development mode features.
        install_workspace_modules: If True, install all modules in workspace.
        extra_launch_args: Additional command line arguments for odoo-bin.
        extra_bootstrap_args: Additional arguments for bootstrap process.
        log_file_path: Path to the log file (None for stdout).
        multithread_worker_count: Number of worker processes.

    Returns:
        int: 0 for success, non-zero for failure.
    """
    godoo_conf = GodooConfig(
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_filter=db_filter,
        odoo_install_folder=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        multithread_worker_count=multithread_worker_count,
        languages="de_DE,en_US",
    )

    launch_cmd = bootstrap_and_prep_launch_cmd(
        godoo_conf=godoo_conf,
        odoo_demo=odoo_demo,
        dev_mode=dev_mode,
        install_workspace_addons=install_workspace_modules,
        extra_launch_args=extra_launch_args,
        extra_bootstrap_args=extra_bootstrap_args,
        log_file_path=log_file_path,
    )

    if not isinstance(launch_cmd, str):
        LOGGER.error("godoo Launch Failed. Bootstrap unsuccessfull. Aborting Launch...")
        return CLI.returner(launch_cmd)

    launch_cmd = re.sub(r"(--dev [\w,]+)(,reload)", r"\1", launch_cmd)  # Remove reload option from CMD String

    LOGGER.info("Starting Data Importer Thread for: '%s'", ", ".join(map(str, load_data_path)))
    loader_thread = threading.Thread(
        target=import_to_odoo,
        name="DataLoader",
        kwargs={
            "read_paths": load_data_path,
            "rpc_host": rpc_host,
            "rpc_database": db_name,
            "rpc_user": rpc_user,
            "rpc_password": rpc_password,
        },
    )
    loader_thread.start()

    LOGGER.info("Launching Odoo")
    return CLI.returner(run_cmd(launch_cmd).returncode)
