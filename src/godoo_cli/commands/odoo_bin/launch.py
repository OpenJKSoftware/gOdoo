"""Odoo instance launch and management module.

This module provides functionality for launching and managing Odoo instances,
including bootstrapping new databases, handling configuration, and managing
the launch process with various options like development mode and worker counts.
"""

import logging
import threading
from pathlib import Path
from typing import Annotated, Optional

import typer

from ...cli_common import CommonCLI
from ...helpers.modules_py import install_base_python_reqs, install_py_reqs_for_modules
from ...helpers.odoo_command import run_odoo_command
from ...helpers.odoo_files import require_odoo_version
from ...models import GodooConfig, GodooModules
from ..rpc import import_to_odoo
from ..source_get import update_odoo_conf
from .bootstrap import bootstrap_and_prep_launch_cmd
from .cli_generate import _launch_command

CLI = CommonCLI()

LOGGER = logging.getLogger(__name__)


def _without_reload(command: list[str]) -> list[str]:
    """Disable Odoo autoreload while an in-process importer is running."""
    return [
        argument.replace(",reload", "") if index and command[index - 1] == "--dev" else argument
        for index, argument in enumerate(command)
    ]


def prepare_runtime(godoo_conf: GodooConfig) -> None:
    """Prepare configuration and Python dependencies without changing a database."""
    require_odoo_version(godoo_conf.odoo_install_folder, ">=19")
    if godoo_conf.odoo_conf_path.exists():
        update_odoo_conf(
            odoo_conf=godoo_conf.odoo_conf_path,
            odoo_main_path=godoo_conf.odoo_install_folder,
            workspace_addon_path=godoo_conf.workspace_addon_path,
            thirdparty_addon_path=godoo_conf.thirdparty_addon_path,
        )
    install_base_python_reqs(godoo_conf.odoo_install_folder)
    module_registry = GodooModules(godoo_conf.addon_paths)
    install_py_reqs_for_modules(list(module_registry.get_modules()), module_registry)


def prepare_odoo(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
) -> None:
    """Prepare configuration and Python dependencies without touching the database."""
    godoo_conf = GodooConfig(
        odoo_install_folder=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        data_dir=data_dir,
    )
    prepare_runtime(godoo_conf)


def launch_odoo(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_filter: Annotated[str, CLI.database.db_filter],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_password: Annotated[str, CLI.database.db_password] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    extra_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args] = None,
    log_file_path: Annotated[Optional[Path], CLI.odoo_launch.log_file_path] = None,
    dev_mode: Annotated[bool, CLI.odoo_launch.dev_mode] = False,
    multithread_worker_count: Annotated[int, CLI.odoo_launch.multithread_worker_count] = 2,
    languages: Annotated[str, CLI.odoo_launch.languages] = "de_DE,en_US",
):
    """Launch Odoo without preparing dependencies or changing database state."""
    require_odoo_version(odoo_main_path, ">=19")
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
        data_dir=data_dir,
        multithread_worker_count=multithread_worker_count,
        languages=languages,
    )
    launch_args = list(extra_args or [])
    if log_file_path is not None:
        log_file_path.unlink(missing_ok=True)
        launch_args.extend(["--logfile", str(log_file_path.absolute())])
    if dev_mode:
        launch_args.extend(["--dev", "xml,qweb,reload"])
    launch_cmd = _launch_command(godoo_conf, launch_args, upgrade_workspace_modules=False)

    LOGGER.info("Launching Odoo on database '%s' using config %s", db_name, odoo_conf_path)
    return CLI.returner(run_odoo_command(launch_cmd).returncode)


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
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
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
    Import launch never resets runtime state implicitly.

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
        data_dir: Odoo data directory containing filestores and runtime data.
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
    require_odoo_version(odoo_main_path, ">=19")
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
        data_dir=data_dir,
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

    if not isinstance(launch_cmd, list):
        LOGGER.error("godoo Launch Failed. Bootstrap unsuccessfull. Aborting Launch...")
        return CLI.returner(launch_cmd)

    # The importer runs in this process and must survive source changes.
    launch_cmd = _without_reload(launch_cmd)

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

    LOGGER.info("Launching Odoo on database '%s' using config %s", db_name, odoo_conf_path)
    return CLI.returner(run_odoo_command(launch_cmd).returncode)
