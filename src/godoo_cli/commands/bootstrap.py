import logging
import os
import re
from pathlib import Path
from typing import Any, List

from ..cli_common import CommonCLI
from ..helpers.bootstrap import _install_py_reqs_by_odoo_cmd
from ..helpers.odoo_files import get_addon_paths, get_odoo_module_paths
from ..helpers.system import run_cmd

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
    db_host: str,
    db_filter: str,
    db_name: str,
    db_user: str,
    db_password: str,
    db_port: str,
    extra_cmd_args: List[str] = None,
    install_base: bool = True,
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
    db_host : str
        db hostname for odoo.conf
    db_name : str
        database name for odoo.conf
    db_user : str
        db user
    db_password : str
        db user password
    db_port : str
        db host port
    extra_cmd_args : List[str], optional
        extra args to pass to odoo-bin, by default None
    install_base : bool, optional
        install base,web, by default True
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
        f"--database {db_name}",
        f"--db_user {db_user}",
        f"--db_password {db_password}",
        f"--db_host {db_host}" if db_host else "",
        f"--db_port {db_port}" if db_host else "",
        f"--db-filter=^{db_filter}$",
    ]

    LOGGER.info("Getting Addon Paths")

    workspace_addons = get_odoo_module_paths(workspace_addon_path)
    if any([re.match(r"( |^)(-i|--init)", i) for i in extra_cmd_args]):
        init_modules = []
    else:
        init_modules = ["base", "web"] if install_base else []
        if install_workspace_modules and workspace_addons:
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
        multithread_worker_count = os.cpu_count() or 0 + 1

    if multithread_worker_count > 0:
        odoo_cmd += [
            "--proxy-mode",
            f"--workers {multithread_worker_count}",
        ]

    odoo_cmd = list(filter(None, odoo_cmd))
    return " ".join(odoo_cmd)


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
    extra_cmd_args=CLI.odoo_launch.extra_cmd_args,
    multithread_worker_count=CLI.odoo_launch.multithread_worker_count,
    languages=CLI.odoo_launch.languages,
    no_install_base=CLI.odoo_launch.no_install_base,
    no_install_workspace_modules=CLI.odoo_launch.no_install_workspace_modules,
):
    """Bootstrap Odoo."""

    addon_paths = get_addon_paths(
        odoo_main_repo=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )

    cmd_string = _boostrap_command(
        odoo_main_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        addon_paths=addon_paths,
        db_host=db_host,
        db_name=db_name,
        db_filter=db_filter,
        db_user=db_user,
        db_password=db_password,
        db_port=db_port,
        extra_cmd_args=extra_cmd_args,
        install_base=not no_install_base,
        install_workspace_modules=not no_install_workspace_modules,
        multithread_worker_count=multithread_worker_count,
        languages=languages,
    )

    # Always update Pip reqs regardless of --no-update-source
    _install_py_reqs_by_odoo_cmd(addon_paths=addon_paths, odoo_bin_cmd=cmd_string)

    LOGGER.info("Launching Bootstrap Commandline")
    ret = run_cmd(cmd_string).returncode
    if ret != 0:
        LOGGER.error("Odoo-Bin Returned %d", ret)
    return CLI.returner(ret)
