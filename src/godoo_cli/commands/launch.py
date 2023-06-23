import logging
import re
import threading
from pathlib import Path
from typing import List

import typer

from ..cli_common import CommonCLI
from ..helpers.odoo_files import get_odoo_module_paths, odoo_bin_get_version
from ..helpers.system import run_cmd
from .bootstrap import bootstrap_odoo
from .rpc import import_to_odoo

CLI = CommonCLI()

LOGGER = logging.getLogger(__name__)


def _launch_command(
    odoo_path: Path,
    odoo_conf_path: Path,
    extra_cmd_args: List[str],
    workspace_addon_path: Path,
    upgrade_workspace_modules: bool = True,
):
    """Build Odoo Launch command

    Parameters
    ----------
    odoo_path : Path
        Path to odoo-bin folder
    odoo_conf_path : Path
        Path to odoo.conf
    extra_cmd_args : List[str]
        extra args to pass to odoo-bin
    workspace_addon_path : Path
        Path to dev workspace addons folder
    upgrade_workspace_modules : bool, optional
        upgrade workspace addons, by default True
    """
    upgrade_addons = [f.name for f in get_odoo_module_paths(workspace_addon_path)] if upgrade_workspace_modules else []
    if any(["-u" in i or "--update" in i for i in extra_cmd_args]):
        upgrade_addons = []
    update_addon_string = "--update " + ",".join(upgrade_addons) if upgrade_addons else ""

    odoo_cmd = [
        str(odoo_path.absolute()) + "/odoo-bin",
        update_addon_string,
        f"-c {str(odoo_conf_path.absolute())}",
    ] + extra_cmd_args
    odoo_cmd = list(filter(None, odoo_cmd))
    return " ".join(odoo_cmd)


def pre_launch(
    odoo_main_path: Path,
    workspace_addon_path: Path,
    thirdparty_addon_path: Path,
    odoo_conf_path: Path,
    db_filter: str,
    db_host: str,
    db_port: int,
    db_name: str,
    db_user: str,
    db_password: str,
    odoo_demo: bool,
    dev_mode: bool,
    multithread_worker_count: int = 0,
    extra_launch_args: List[str] = None,
    extra_bootstrap_args: List[str] = None,
    log_file_path: Path = None,
    install_workspace_addons: bool = True,
    install_base: bool = True,
    launch_or_bootstrap: bool = False,
):
    """Start Bootstrap if no config file is found. And return Launch CMD.

    Parameters
    ----------
    odoo_main_path : Path
        Path to Odoo-bin folder
    workspace_addon_path : Path
        Path to workspace addons
    thirdparty_addon_path : Path
        path to thirdparty addons folder
    odoo_conf_path : Path
        path to odoo conf
    db_filter : str
        odoo.conf db_filter
    db_host : str
        database host url. Empty string for Unix sock
    db_port : int
        database port. 0 for unix sock
    db_name : str
        odoo main database name
    db_user : str
        database user
    db_password : str
        database password
    odoo_demo : bool
        if false, add --without-demo to bootstrap
    dev_mode : bool
        add --dev... to cmd
    install_base : bool
        install web, and base
    install_workspace_addons : bool
        install all modules in workspace folder
    extra_args : List[str]
        extra launch CMD args
    extra_bootstrap_args : List[str]
        extra bootstrap cmd args
    log_file_path : Path
        path to odoo.log (stdout log if empty)
    multithread_worker_count : int
        count of multithread workser
    launch_or_bootstrap: bool, optional
        Only return launch cmd if bootstrap did not run

    Returns
    -------
    Union[int,str]
        Int return code of bootstrap if not 0 else launch cmd as string
    """
    LOGGER.info("Starting godoo Init Script")

    extra_odoo_args = []
    if log_file_path is not None:
        log_file_path.unlink(missing_ok=True)
        extra_odoo_args.append("--logfile " + str(log_file_path.absolute()))

    bootstraped = odoo_conf_path.exists()
    LOGGER.info("Bootstrap Flag Status: %s", bootstraped)
    ret = ""
    if not bootstraped:
        _extra_bootstrap_args = extra_odoo_args.copy()
        if ea := extra_bootstrap_args:
            _extra_bootstrap_args += ea
        if not odoo_demo:
            _extra_bootstrap_args += ["--without-demo all"]
        if not install_base:
            install_workspace_addons = False
        ret = bootstrap_odoo(
            db_name=db_name,
            db_filter=db_filter,
            db_user=db_user,
            db_password=db_password,
            db_host=db_host,
            db_port=db_port,
            thirdparty_addon_path=thirdparty_addon_path,
            odoo_main_path=odoo_main_path,
            odoo_conf_path=odoo_conf_path,
            extra_cmd_args=_extra_bootstrap_args,
            no_install_base=not install_base,
            no_install_workspace_modules=not install_workspace_addons,
            multithread_worker_count=multithread_worker_count,
        )
        bootstraped = ret == 0
        if not bootstraped:
            return ret
        if install_workspace_addons and bootstraped:
            install_workspace_addons = False

        if launch_or_bootstrap:
            return

    if ea := extra_launch_args:
        extra_odoo_args += ea

    odoo_main_path = odoo_main_path
    odoo_version = odoo_bin_get_version(odoo_main_path)

    if dev_mode:
        extra_odoo_args.append("--dev xml,qweb,reload")
        if "16.0" in odoo_version:
            extra_odoo_args[-1] += ",werkzeug"

    return _launch_command(
        odoo_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        extra_cmd_args=extra_odoo_args,
        workspace_addon_path=workspace_addon_path,
        upgrade_workspace_modules=install_workspace_addons,
    )


@CLI.unpacker
@CLI.arg_annotator
def launch_odoo(
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
    odoo_demo=CLI.odoo_launch.odoo_demo,
    dev_mode=CLI.odoo_launch.dev_mode,
    no_install_base=CLI.odoo_launch.no_install_base,
    no_install_workspace_modules=CLI.odoo_launch.no_install_workspace_modules,
    extra_args=CLI.odoo_launch.extra_cmd_args,
    extra_bootstrap_args=CLI.odoo_launch.extra_cmd_args,
    log_file_path=CLI.odoo_launch.log_file_path,
    multithread_worker_count=CLI.odoo_launch.multithread_worker_count,
):
    """
    Launch Odoo, Bootstrap if bootstrapflag is not present.
    """

    launch_cmd = pre_launch(
        odoo_main_path=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        odoo_conf_path=odoo_conf_path,
        db_filter=db_filter,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        odoo_demo=odoo_demo,
        dev_mode=dev_mode,
        install_base=not no_install_base,
        install_workspace_addons=not no_install_workspace_modules,
        extra_launch_args=extra_args,
        extra_bootstrap_args=extra_bootstrap_args,
        log_file_path=log_file_path,
        multithread_worker_count=multithread_worker_count,
    )

    if not isinstance(launch_cmd, str):
        LOGGER.error("godoo Launch Failed. Bootstrap unsuccessfull. Aborting Launch...")
        return CLI.returner(launch_cmd)

    LOGGER.info("Launching Odoo")
    return CLI.returner(run_cmd(launch_cmd).returncode)


@CLI.arg_annotator
def launch_import(
    load_data_path: List[Path] = typer.Argument(
        ...,
        help="Starts Async Importer Job with provided path(s).",
    ),
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
    rpc_host=CLI.rpc.rpc_host,
    rpc_user=CLI.rpc.rpc_user,
    rpc_password=CLI.rpc.rpc_password,
    odoo_demo=CLI.odoo_launch.odoo_demo,
    dev_mode=CLI.odoo_launch.dev_mode,
    no_install_base=CLI.odoo_launch.no_install_base,
    no_install_workspace_modules=CLI.odoo_launch.no_install_workspace_modules,
    extra_launch_args=CLI.odoo_launch.extra_cmd_args,
    extra_bootstrap_args=CLI.odoo_launch.extra_cmd_args,
    log_file_path=CLI.odoo_launch.log_file_path,
    multithread_worker_count=CLI.odoo_launch.multithread_worker_count,
):
    """Bootstrap and Start odoo. Launches RPC import in second thread."""

    launch_cmd = pre_launch(
        odoo_main_path=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        odoo_conf_path=odoo_conf_path,
        db_filter=db_filter,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        odoo_demo=odoo_demo,
        dev_mode=dev_mode,
        install_base=not no_install_base,
        install_workspace_addons=not no_install_workspace_modules,
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
