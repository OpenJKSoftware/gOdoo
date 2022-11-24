import logging
import re
from pathlib import Path
from typing import List

import typer

from ..helpers import run_cmd
from ..helpers.cli import typer_retuner, typer_unpacker
from ..helpers.odoo_files import _get_python_requirements_of_modules, get_addon_paths, get_odoo_module_paths
from .source_get import get_source

LOGGER = logging.getLogger(__name__)


def _install_py_reqs_for_modules(addon_paths: List[Path], module_names: List[str] = None):
    """Install Python Requirements mentioned in odoo module manifests of given modules

    Parameters
    ----------
    addon_paths : List[Path]
        odoo-bin addons path
    module_names : List[str], Optional
        List of modules to filter for, by default "all available modules"

    Returns
    -------
    CompletedProcess
    """
    py_reqs = _get_python_requirements_of_modules(addon_paths=addon_paths, filter_module_names=module_names)
    if py_reqs:
        LOGGER.info("Installing Python requirements: %s", ", ".join(py_reqs))
        return run_cmd(f"pip install {' '.join(py_reqs)}")
    LOGGER.debug("No py Requirements to be installed")


def _install_py_reqs_by_odoo_cmd(addon_paths: List[Path], odoo_bin_cmd: str):
    """Install Python reqs for modules mentioned in odoo-bin commandline --init or -i directives.

    Parameters
    ----------
    addon_paths : List[Path]
        odoo-bin addons-path
    odoo_bin_cmd : str
        odoo-bin commandline

    Returns
    -------
    CompletedProcess
    """
    install_modules = []
    for m in re.finditer(r'(--init|-i) "?([^ \n]+)"?', odoo_bin_cmd):
        install_modules += m.group(2).split(",")
    if install_modules:
        return _install_py_reqs_for_modules(addon_paths=addon_paths, module_names=install_modules)


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
    multithread_worker_count: int = 9,
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
    db_host : str
        db hostname for odoo.conf
    db_filter : str
        db filter for odoo.conf
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
        "--log-level info",
        "--limit-time-cpu 360",
        "--limit-time-real 420",
        "--data-dir /var/lib/odoo",
        "--stop-after-init",
        f"--addons-path '{addon_paths}'",
    ]
    odoo_cmd = base_cmds + db_command
    if extra_cmd_args:
        odoo_cmd.append(" ".join(extra_cmd_args))
    if multithread_worker_count > 0:
        odoo_cmd += [
            "--proxy-mode",
            f"--workers {multithread_worker_count}",
        ]

    odoo_cmd = list(filter(None, odoo_cmd))
    return " ".join(odoo_cmd)


@typer_unpacker
def bootstrap_odoo(
    ctx: typer.Context,
    thirdparty_addon_path: Path = typer.Option(
        ..., envvar="ODOO_THIRDPARTY_LOCATION", help="folder that contains thirdparty repos like OCA"
    ),
    db_host: str = typer.Option(..., envvar="ODOO_DB_HOST", help="db hostname"),
    db_filter: str = typer.Option(..., envvar="ODOO_DB_FILTER", help="database filter for odoo_conf"),
    db_name: str = typer.Option(..., envvar="ODOO_MAIN_DB", help="launch database name"),
    db_user: str = typer.Option(..., envvar="ODOO_DB_USER", help="db user"),
    db_password: str = typer.Option(..., envvar="ODOO_DB_PASSWORD", help="db password"),
    db_port: str = typer.Option(..., envvar="ODOO_DB_PORT", help="db host port"),
    extra_cmd_args: List[str] = typer.Option(None, help="extra agruments to pass to odoo-bin"),
    install_base: bool = typer.Option(True, help="install base,web modules"),
    install_workspace_modules: bool = typer.Option(True, help="install all modules found in workspace_path"),
    multithread_worker_count: int = typer.Option(9, help="count of worker threads. will enable proxy_mode if >0"),
    languages: str = typer.Option("de_DE,en_US", help="languages to load by default"),
    update_source: bool = typer.Option(True, help="Update Odoo Source and Thirdparty Addons"),
    addons_remove_unspecified: bool = typer.Option(
        True, help="Cleanup/Remove unspecified Addons if 'update_source' is true"
    ),
):
    """Bootstrap Odoo."""
    if update_source:
        get_source(ctx=ctx, remove_unspecified_addons=addons_remove_unspecified)

    addon_paths = get_addon_paths(
        odoo_main_repo=ctx.obj.odoo_main_path,
        workspace_addon_path=ctx.obj.workspace_addon_path,
        zip_addon_path=thirdparty_addon_path / "custom",  # !Todo Pull out into variable
        thirdparty_addon_path=thirdparty_addon_path,
    )

    cmd_string = _boostrap_command(
        odoo_main_path=ctx.obj.odoo_main_path,
        odoo_conf_path=ctx.obj.odoo_conf_path,
        workspace_addon_path=ctx.obj.workspace_addon_path,
        addon_paths=addon_paths,
        db_host=db_host,
        db_name=db_name,
        db_filter=db_filter,
        db_user=db_user,
        db_password=db_password,
        db_port=db_port,
        extra_cmd_args=extra_cmd_args,
        install_base=install_base,
        install_workspace_modules=install_workspace_modules,
        multithread_worker_count=multithread_worker_count,
        languages=languages,
    )

    if update_source:
        _install_py_reqs_by_odoo_cmd(addon_paths=addon_paths, odoo_bin_cmd=cmd_string)

    LOGGER.info("Launching Bootstrap Commandline")
    ret = run_cmd(cmd_string).returncode
    if ret == 0:
        ctx.obj.bootstrap_flag_location.touch()
    else:
        LOGGER.error("Odoo-Bin Returned %d", ret)
    return typer_retuner(ret)
