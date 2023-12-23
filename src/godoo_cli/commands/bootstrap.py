import logging
import os
import re
from pathlib import Path
from typing import Any, List, Optional

import typer
from typing_extensions import Annotated

from ..cli_common import CommonCLI
from ..helpers.bootstrap import _install_py_reqs_by_odoo_cmd
from ..helpers.odoo_files import get_addon_paths, get_odoo_module_paths
from ..helpers.system import run_cmd
from .db.connection import DBConnection

CLI = CommonCLI()

LOGGER = logging.getLogger(__name__)


def _add_default_argument(cmd_list: List[str], arg: str, arg_val: Any):
    if not any([arg in s for s in cmd_list]):
        cmd_list.append(f'{arg}="{arg_val}"')


def _boostrap_command(
    odoo_main_path: Path,
    odoo_conf_path: Path,
    addon_paths: List[Path],
    workspace_addon_path: Path,
    db_filter: str,
    db_connection: DBConnection,
    extra_cmd_args: Optional[List[str]] = None,
    install_workspace_modules: bool = True,
    multithread_worker_count: int = -1,
    languages: str = "de_DE,en_US",
) -> str:
    """
    Generate Bootstrap Command.

    Parameters
    ----------
    odoo_main_path : Path
        folder with odoo-bin
    odoo_conf_path : Path
        path to odoo.conf
    addon_paths : List[Path]
        odoo bin --addons-path
    workspace_addon_path : Path
        path to addons in dev repo
    db_filter : str
        db filter for odoo.conf
    db_connection : DBConnection
        DB Connection
    extra_cmd_args : List[str], optional
        extra args to pass to odoo-bin, by default None
    install_workspace_modules : bool, optional
        install all modules found in workspace_path, by default True
    multithread_worker_count : int, optional
        cound of threads. if >0 cli also sets proxy mode flag, by default 9
    languages : str, optional
        languages to load, by default "de_DE,en_US"

    Returns
    -------
    str
        odoo-bin command
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
    if not re.search(r"(-i|--init) ", extra_cmd_args_str):
        if install_workspace_modules:
            if workspace_addons := get_odoo_module_paths(workspace_addon_path):
                init_modules += [f.name for f in workspace_addons]
    init_cmd = "--init " + ",".join(init_modules) if init_modules else ""

    addon_paths = [str(p.absolute()) for p in addon_paths]
    addon_paths = ", ".join(list(filter(None, addon_paths)))

    base_cmds = [
        str(odoo_main_path.absolute()) + "/odoo-bin",
        init_cmd,
        f"--config {str(odoo_conf_path.absolute())}",
        "--save",
        f"--load-language {languages}",
        "--stop-after-init",
        f"--addons-path '{addon_paths}'",
    ]
    odoo_cmd = base_cmds + db_command
    if extra_cmd_args:
        odoo_cmd += extra_cmd_args

    _add_default_argument(cmd_list=odoo_cmd, arg="--data-dir", arg_val="/var/lib/odoo")

    if multithread_worker_count == -1:
        multithread_worker_count = (os.cpu_count() or 2) / 2

    if multithread_worker_count > 0:
        odoo_cmd += [
            "--proxy-mode",
            f"--workers {int( multithread_worker_count )}",
        ]

    odoo_cmd = list(filter(None, odoo_cmd))
    cmd_str = " ".join(odoo_cmd)
    return cmd_str


@CLI.unpacker
@CLI.arg_annotator
def bootstrap_odoo(
    odoo_main_path=CLI.odoo_paths.bin_path,
    workspace_addon_path=CLI.odoo_paths.workspace_addon_path,
    thirdparty_addon_path=CLI.odoo_paths.thirdparty_addon_path,
    odoo_conf_path=CLI.odoo_paths.conf_path,
    db_filter=CLI.database.db_filter,
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
    extra_cmd_args=CLI.odoo_launch.extra_cmd_args_bootstrap,
    multithread_worker_count=CLI.odoo_launch.multithread_worker_count,
    languages=CLI.odoo_launch.languages,
    install_workspace_modules=CLI.odoo_launch.install_workspace_modules,
    install_base_modules: Annotated[
        bool,
        typer.Option(
            ...,
            help="Install base and web modules if no other -i or -u is present in Bootstrap",
            rich_help_panel="Odoo",
        ),
    ] = True,
):
    """Bootstrap Odoo."""

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
