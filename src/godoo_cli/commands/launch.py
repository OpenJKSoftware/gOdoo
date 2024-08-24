import logging
import re
import threading
from pathlib import Path
from typing import List, Optional

import typer

from ..cli_common import CommonCLI
from ..helpers.modules import godooModules
from ..helpers.odoo_files import odoo_bin_get_version
from ..helpers.system import run_cmd
from .bootstrap import bootstrap_odoo
from .db.connection import DBConnection
from .db.query import DB_BOOTSTRAP_STATUS, _is_bootstrapped
from .rpc import import_to_odoo
from .source_get import py_depends_by_db, update_odoo_conf

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
    upgrade_addons = (
        [f.name for f in godooModules(workspace_addon_path).get_modules()] if upgrade_workspace_modules else []
    )
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


def bootstrap_and_prep_launch_cmd(
    odoo_main_path: Path,
    workspace_addon_path: Path,
    thirdparty_addon_path: Path,
    odoo_conf_path: Path,
    db_filter: str,
    db_connection: DBConnection,
    odoo_demo: bool,
    dev_mode: bool,
    multithread_worker_count: int = 0,
    extra_launch_args: Optional[List[str]] = None,
    extra_bootstrap_args: Optional[List[str]] = None,
    log_file_path: Optional[Path] = None,
    install_workspace_addons: bool = True,
    launch_or_bootstrap: bool = False,
    languages: str = "de_DE,en_US",
):
    """Start Bootstrap if database is not bootstrapped. Install Py Depends And return Launch CMD.

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
    db_connection : DBConnection
        DBConnection object
    odoo_demo : bool
        if false, add --without-demo to bootstrap
    dev_mode : bool
        add --dev... to cmd
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
    languages : str, optional
        languages to load, by default "de_DE,en_US"

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

    bootstraped = _is_bootstrapped(db_connection)
    LOGGER.info("Bootstrap Flag Status: '%s'", bootstraped.value)
    ret = ""
    did_bootstrap = False
    if bootstraped != DB_BOOTSTRAP_STATUS.BOOTSTRAPPED:
        _extra_bootstrap_args = extra_odoo_args.copy()
        if ea := extra_bootstrap_args:
            _extra_bootstrap_args += ea
        if not odoo_demo:
            _extra_bootstrap_args += ["--without-demo all"]
        ret = bootstrap_odoo(
            **db_connection.cli_dict,
            db_filter=db_filter,
            thirdparty_addon_path=thirdparty_addon_path,
            odoo_main_path=odoo_main_path,
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
        bootstraped != DB_BOOTSTRAP_STATUS.BOOTSTRAPPED
        and _is_bootstrapped(db_connection) != DB_BOOTSTRAP_STATUS.BOOTSTRAPPED
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
    install_workspace_modules=CLI.odoo_launch.install_workspace_modules,
    extra_args=CLI.odoo_launch.extra_cmd_args,
    extra_bootstrap_args=CLI.odoo_launch.extra_cmd_args_bootstrap,
    log_file_path=CLI.odoo_launch.log_file_path,
    multithread_worker_count=CLI.odoo_launch.multithread_worker_count,
    languages=CLI.odoo_launch.languages,
):
    """
    Launch Odoo, Bootstrap if db is empty.
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
    install_workspace_modules=CLI.odoo_launch.install_workspace_modules,
    extra_launch_args=CLI.odoo_launch.extra_cmd_args,
    extra_bootstrap_args=CLI.odoo_launch.extra_cmd_args_bootstrap,
    log_file_path=CLI.odoo_launch.log_file_path,
    multithread_worker_count=CLI.odoo_launch.multithread_worker_count,
):
    """Bootstrap and Start odoo. Launches RPC import in second thread."""
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
