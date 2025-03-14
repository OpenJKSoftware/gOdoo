"""Module for bootstrapping Odoo instances.

This module provides functionality to bootstrap Odoo instances by installing
required Python dependencies, setting up database connections, and initializing
the Odoo environment with necessary modules and configurations.
"""

import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from ..cli_common import CommonCLI
from ..helpers.modules import GodooModules, get_addon_paths
from ..helpers.modules_py import _install_py_reqs_by_odoo_cmd
from ..helpers.system import run_cmd
from .db.connection import DBConnection
from .shell.shell import odoo_shell_run_script

CLI = CommonCLI()

LOGGER = logging.getLogger(__name__)


def _add_default_argument(cmd_list: list[str], arg: str, arg_val: Any):
    """Add a default argument to the command list if not already present.

    Args:
        cmd_list: List of command arguments.
        arg: Argument name to add.
        arg_val: Value for the argument.
    """
    if not any(arg in s for s in cmd_list):
        cmd_list.append(f'{arg}="{arg_val}"')


def _boostrap_command(
    odoo_main_path: Path,
    odoo_conf_path: Path,
    addon_paths: list[Path],
    workspace_addon_path: Path,
    db_filter: str,
    db_connection: DBConnection,
    extra_cmd_args: Optional[list[str]] = None,
    install_workspace_modules: bool = True,
    multithread_worker_count: int = -1,
    languages: str = "de_DE,en_US",
) -> str:
    """Generate bootstrap command for Odoo initialization.

    This function constructs the Odoo bootstrap command with all necessary parameters
    including database configuration, addon paths, and worker settings.

    Args:
        odoo_main_path: Folder containing odoo-bin.
        odoo_conf_path: Path to odoo.conf.
        addon_paths: List of paths for odoo-bin --addons-path.
        workspace_addon_path: Path to addons in dev repo.
        db_filter: Database filter for odoo.conf.
        db_connection: Database connection details.
        extra_cmd_args: Extra args to pass to odoo-bin.
        install_workspace_modules: Whether to install all modules found in workspace_path.
        multithread_worker_count: Number of worker threads. If >0, also sets proxy mode flag.
        languages: Languages to load, comma-separated.

    Returns:
        The complete odoo-bin command string.
    """
    LOGGER.info("Generating Bootstrap Command")

    odoo_conf_path.parent.mkdir(parents=True, exist_ok=True)

    db_command = [
        f"--database {db_connection.db_name}",
        f"--db_user {db_connection.username}",
        f"--db_password {db_connection.password}",
        f"--db_host {db_connection.hostname}" if db_connection.hostname else "",
        f"--db_port {db_connection.port}" if db_connection.hostname else "",
        f"--db-filter=^{db_filter}$",
    ]

    LOGGER.info("Getting Addon Paths")

    init_modules = []
    extra_cmd_args_str = " ".join(extra_cmd_args or [])
    if install_workspace_modules and not re.search(r"(-i|--init) ", extra_cmd_args_str):
        workspace_modules = GodooModules([workspace_addon_path])
        if workspace_addons := workspace_modules.get_modules():
            init_modules += [f.name for f in workspace_addons]
    init_cmd = "--init " + ",".join(init_modules) if init_modules else ""

    addon_paths_str_list = [str(p.absolute()) for p in addon_paths if p and p.exists()]
    addon_paths_str = ", ".join(addon_paths_str_list)

    base_cmds = [
        str(odoo_main_path.absolute()) + "/odoo-bin",
        init_cmd,
        f"--config {odoo_conf_path.absolute()!s}",
        "--save",
        f"--load-language {languages}",
        "--stop-after-init",
        f"--addons-path '{addon_paths_str}'",
    ]
    odoo_cmd = base_cmds + db_command
    if extra_cmd_args:
        odoo_cmd += extra_cmd_args

    _add_default_argument(cmd_list=odoo_cmd, arg="--data-dir", arg_val="/var/lib/odoo")

    if multithread_worker_count == -1:
        multithread_worker_count = int(os.cpu_count() or 2 / 2)

    if multithread_worker_count > 0:
        odoo_cmd += [
            "--proxy-mode",
            f"--workers {int(multithread_worker_count)}",
        ]

    odoo_cmd = list(filter(None, odoo_cmd))
    cmd_str = " ".join(odoo_cmd)
    return cmd_str


def bootstrap_odoo(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_filter: Annotated[str, CLI.database.db_filter],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host],
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    extra_cmd_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
    multithread_worker_count: Annotated[int, CLI.odoo_launch.multithread_worker_count] = 2,
    languages: Annotated[str, CLI.odoo_launch.languages] = "de_DE,en_US",
    install_workspace_modules: Annotated[bool, CLI.odoo_launch.install_workspace_modules] = True,
    install_base_modules: Annotated[
        bool,
        typer.Option(
            help="Install base and web modules if no other -i or -u is present in Bootstrap",
            rich_help_panel="Odoo",
        ),
    ] = True,
    banner_text: Annotated[str, CLI.odoo_launch.banner_text] = "",
    banner_bg_color: Annotated[str, CLI.odoo_launch.banner_bg_color] = "green",
):
    """Bootstrap an Odoo instance with specified configuration."""
    addon_paths = get_addon_paths(
        odoo_main_repo=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )

    db_connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name=db_name,
    )

    cmd_string = _boostrap_command(
        odoo_main_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        addon_paths=addon_paths,
        db_filter=db_filter,
        db_connection=db_connection,
        extra_cmd_args=extra_cmd_args,
        install_workspace_modules=install_workspace_modules,
        multithread_worker_count=multithread_worker_count,
        languages=languages,
    )
    if not install_base_modules:
        cmd_string = re.sub(r"--init base,web", "", cmd_string)

    # Always update Pip reqs regardless of --no-update-source
    _install_py_reqs_by_odoo_cmd(addon_paths=addon_paths, odoo_bin_cmd=cmd_string)

    LOGGER.info("Launching Bootstrap Commandline")
    ret = run_cmd(cmd_string).returncode
    if ret != 0:
        LOGGER.error("Odoo-Bin Returned %d", ret)
        return CLI.returner(ret)
    if banner_text:
        os.environ["ODOO_BANNER_TEXT"] = banner_text
        os.environ["ODOO_BANNER_BG_COLOR"] = banner_bg_color
        odoo_shell_run_script(
            script_name="odoo_banner",
            odoo_main_path=odoo_main_path,
            odoo_conf_path=odoo_conf_path,
            db_name=db_name,
            db_user=db_user,
            db_host=db_host,
            db_port=db_port,
            db_password=db_password,
        )
    return CLI.returner(ret)
