"""Odoo instance launch and management module.

This module provides functionality for launching and managing Odoo instances,
including bootstrapping new databases, handling configuration, and managing
the launch process with various options like development mode and worker counts.
"""

import logging
import re
import threading
from pathlib import Path
from typing import Annotated, Optional, Union

import typer

from ..cli_common import CommonCLI
from ..helpers.modules import GodooModules
from ..helpers.odoo_files import odoo_bin_get_version
from ..helpers.system import run_cmd
from .bootstrap import bootstrap_odoo
from .db.connection import DBConnection
from .db.query import DbBootstrapStatus, _is_bootstrapped
from .rpc import import_to_odoo
from .source_get import py_depends_by_db, update_odoo_conf

CLI = CommonCLI()

LOGGER = logging.getLogger(__name__)


def _launch_command(
    odoo_path: Path,
    odoo_conf_path: Path,
    extra_cmd_args: list[str],
    workspace_addon_path: Path,
    upgrade_workspace_modules: bool = True,
) -> str:
    """Build the Odoo launch command with all necessary arguments.

    This function constructs the command line string used to launch Odoo,
    including handling module upgrades and configuration paths.

    Args:
        odoo_path: Path to the Odoo installation directory containing odoo-bin.
        odoo_conf_path: Path to the Odoo configuration file.
        extra_cmd_args: Additional command line arguments to pass to odoo-bin.
        workspace_addon_path: Path to the workspace addons directory.
        upgrade_workspace_modules: If True, automatically upgrade all modules in workspace.

    Returns:
        str: The complete command string to launch Odoo.
    """
    upgrade_addons = (
        [f.name for f in GodooModules(workspace_addon_path).get_modules()] if upgrade_workspace_modules else []
    )
    if any(arg in ("-u", "--update") for arg in extra_cmd_args):
        upgrade_addons = []
    update_addon_string = "--update " + ",".join(upgrade_addons) if upgrade_addons else ""

    odoo_cmd = [
        str(odoo_path.absolute()) + "/odoo-bin",
        update_addon_string,
        f"-c {odoo_conf_path.absolute()!s}",
        *extra_cmd_args,
    ]
    odoo_cmd = list(filter(None, odoo_cmd))
    return " ".join(odoo_cmd)


def bootstrap_and_prep_launch_cmd(  # noqa: C901
    odoo_main_path: Path,
    workspace_addon_path: Path,
    thirdparty_addon_path: Path,
    odoo_conf_path: Path,
    db_filter: str,
    db_connection: DBConnection,
    odoo_demo: bool,
    dev_mode: bool,
    multithread_worker_count: int = 0,
    extra_launch_args: Optional[list[str]] = None,
    extra_bootstrap_args: Optional[list[str]] = None,
    log_file_path: Optional[Path] = None,
    install_workspace_addons: bool = True,
    launch_or_bootstrap: bool = False,
    languages: str = "de_DE,en_US",
) -> Union[int, str]:
    """Bootstrap an Odoo instance if needed and prepare the launch command.

    This function handles the complete process of preparing an Odoo instance for launch:
    1. Checks if the database exists and is bootstrapped
    2. Bootstraps the database if needed
    3. Installs Python dependencies
    4. Updates odoo.conf with current addon paths
    5. Prepares the launch command with appropriate options

    Args:
        odoo_main_path: Path to the Odoo installation directory.
        workspace_addon_path: Path to workspace addons directory.
        thirdparty_addon_path: Path to thirdparty addons directory.
        odoo_conf_path: Path to odoo.conf file.
        db_filter: Database filter pattern for odoo.conf.
        db_connection: Database connection details.
        odoo_demo: If True, load demo data during bootstrap.
        dev_mode: If True, enable development mode features.
        multithread_worker_count: Number of worker processes (0 for single process).
        extra_launch_args: Additional arguments for the launch command.
        extra_bootstrap_args: Additional arguments for the bootstrap process.
        log_file_path: Path to the log file (None for stdout).
        install_workspace_addons: If True, install all modules found in workspace.
        launch_or_bootstrap: If True, only return launch command if bootstrap didn't run.
        languages: Comma-separated list of languages to install.

    Returns:
        Union[int, str]: Either a non-zero error code if bootstrap failed,
            or the launch command string if successful.
    """
    LOGGER.info("Starting godoo Init Script")

    extra_odoo_args = []
    if log_file_path is not None:
        log_file_path.unlink(missing_ok=True)
        extra_odoo_args.append("--logfile " + str(log_file_path.absolute()))

    bootstraped = _is_bootstrapped(db_connection)
    LOGGER.info("Bootstrap Flag Status: '%s'", bootstraped.value)
    ret = ""
    did_bootstrap = False
    if bootstraped != DbBootstrapStatus.BOOTSTRAPPED:
        _extra_bootstrap_args = extra_odoo_args.copy()
        if ea := extra_bootstrap_args:
            _extra_bootstrap_args += ea
        if not odoo_demo:
            _extra_bootstrap_args += ["--without-demo all"]
        ret = bootstrap_odoo(
            **db_connection.cli_dict,
            db_filter=db_filter,
            odoo_main_path=odoo_main_path,
            workspace_addon_path=workspace_addon_path,
            thirdparty_addon_path=thirdparty_addon_path,
            odoo_conf_path=odoo_conf_path,
            extra_cmd_args=_extra_bootstrap_args,
            install_workspace_modules=install_workspace_addons,
            multithread_worker_count=multithread_worker_count,
            languages=languages,
        )
        bootstraped = ret == 0
        if not bootstraped or launch_or_bootstrap:
            return ret
        did_bootstrap = True
        install_workspace_addons = False

    if ea := extra_launch_args:
        extra_odoo_args += ea

    odoo_main_path = odoo_main_path
    odoo_version = odoo_bin_get_version(odoo_main_path)

    if (
        bootstraped != DbBootstrapStatus.BOOTSTRAPPED
        and _is_bootstrapped(db_connection) != DbBootstrapStatus.BOOTSTRAPPED
    ):
        LOGGER.error("404 Database not found. Aborting Launch...")
        return 404

    update_odoo_conf(
        odoo_conf=odoo_conf_path,
        odoo_main_path=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )
    if not did_bootstrap:
        py_depends_by_db(
            odoo_main_path=odoo_main_path,
            workspace_addon_path=workspace_addon_path,
            thirdparty_addon_path=thirdparty_addon_path,
            **db_connection.cli_dict,
        )

    if dev_mode:
        extra_odoo_args.append("--dev xml,qweb,reload")
        if odoo_version.major == 16:
            extra_odoo_args[-1] += ",werkzeug"

    if multithread_worker_count == 0:
        extra_odoo_args.append("--workers 0")

    return _launch_command(
        odoo_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        extra_cmd_args=extra_odoo_args,
        workspace_addon_path=workspace_addon_path,
        upgrade_workspace_modules=install_workspace_addons,
    )


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
    extra_args: Annotated[Optional[str], CLI.odoo_launch.extra_cmd_args] = None,
    extra_bootstrap_args: Annotated[Optional[str], CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
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
    db_connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name=db_name,
    )
    launch_cmd = bootstrap_and_prep_launch_cmd(
        odoo_main_path=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        odoo_conf_path=odoo_conf_path,
        db_filter=db_filter,
        db_connection=db_connection,
        odoo_demo=odoo_demo,
        dev_mode=dev_mode,
        install_workspace_addons=install_workspace_modules,
        extra_launch_args=extra_args,
        extra_bootstrap_args=extra_bootstrap_args,
        log_file_path=log_file_path,
        multithread_worker_count=multithread_worker_count,
        languages=languages,
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
    extra_launch_args: Annotated[Optional[str], CLI.odoo_launch.extra_cmd_args] = None,
    extra_bootstrap_args: Annotated[Optional[str], CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
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
    db_connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name=db_name,
    )

    launch_cmd = bootstrap_and_prep_launch_cmd(
        odoo_main_path=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        odoo_conf_path=odoo_conf_path,
        db_filter=db_filter,
        db_connection=db_connection,
        odoo_demo=odoo_demo,
        dev_mode=dev_mode,
        install_workspace_addons=install_workspace_modules,
        extra_launch_args=extra_launch_args,
        extra_bootstrap_args=extra_bootstrap_args,
        log_file_path=log_file_path,
        multithread_worker_count=multithread_worker_count,
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
