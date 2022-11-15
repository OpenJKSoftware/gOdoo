import logging
import os
import threading
from pathlib import Path
from typing import List

import typer

from ..commands.rpc.cli import rpc_callback
from ..helpers.cli import typer_retuner, typer_unpacker
from ..helpers.odoo_files import get_odoo_addons_in_folder
from . import bootstrap, makedev, rpc

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
        [f.name for f in get_odoo_addons_in_folder(workspace_addon_path)] if upgrade_workspace_modules else []
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


def _launch(
    odoo_path: Path,
    odoo_conf_path: Path,
    extra_cmd_args: List[str],
    workspace_addon_path: Path,
    upgrade_workspace_modules: bool = True,
):
    """Launches Odoo

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
    cmd_string = _launch_command(
        odoo_path=odoo_path,
        odoo_conf_path=odoo_conf_path,
        extra_cmd_args=extra_cmd_args,
        workspace_addon_path=workspace_addon_path,
        upgrade_workspace_modules=upgrade_workspace_modules,
    )
    LOGGER.info("Launching Odoo: '%s'", cmd_string)
    return os.system(cmd_string)


@typer_unpacker
def launch_odoo(
    ctx: typer.Context,
    launch: bool = typer.Option(True, help="Launch after Bootstrap"),
    odoo_demo: bool = typer.Option(False, help="Load Demo Data"),
    dev_mode: bool = typer.Option(False, help="Pass --dev xml,qweb,reload to odoo"),
    install_modules: bool = typer.Option(True, help="Install Modules"),
    install_workspace_addons: bool = typer.Option(True, help="Install Workspace addons"),
    update_source: bool = typer.Option(True, help="Git Pull Addons"),
    addons_remove_unspecified: bool = typer.Option(True, help="Cleanup/Remove unspecified Addons"),
    load_data_path: Path = typer.Option(None, help="Starts Async Importer Job with provided path"),
    prep_stage: bool = typer.Option(False, help="Starts Async Stage Prepper"),
    extra_args: List[str] = typer.Option([], help="Extra args to Pass to odoo Launch"),
    extra_bootstrap_args: List[str] = typer.Option([], help="Extra args to Pass to odoo Bootstrap"),
    log_file_path: Path = typer.Option(None, dir_okay=False, writable=True, help="Logfile Path"),
    multithread_worker_count: int = typer.Option(9, help="count of worker threads. will enable proxy_mode if >0"),
):
    """
    Bootstrap Odoo and Launch with set options.
    """
    LOGGER.info("Starting Wodoo Odoo Init Script")

    extra_odoo_args = []
    if not log_file_path is None:
        log_file_path.unlink(missing_ok=True)
        extra_odoo_args.append("--logfile " + str(log_file_path.absolute()))

    if ctx.obj.odoo_conf_path.exists() and prep_stage:
        makedev.makedev_config(ctx)

    LOGGER.info("Bootstrap Flag Status: %s", ctx.obj.bootstrap_flag_location.exists())
    bootstraped = False
    ret = ""
    if not ctx.obj.bootstrap_flag_location.exists():
        extra_odoo_args_bootstrap = extra_odoo_args.copy()
        if ea := extra_bootstrap_args:
            extra_odoo_args_bootstrap += ea
        if not odoo_demo:
            extra_odoo_args_bootstrap += ["--without-demo all"]

        ret = bootstrap.bootstrap_odoo(
            ctx=ctx,
            extra_cmd_args=extra_odoo_args_bootstrap,
            install_base=install_modules,
            install_workspace_modules=install_workspace_addons and install_modules,
            multithread_worker_count=multithread_worker_count,
            update_source=update_source,
            addons_remove_unspecified=addons_remove_unspecified,
        )
        install_workspace_addons = False
        bootstraped = True

    if ea := extra_args:
        extra_odoo_args += ea

    if dev_mode:
        extra_odoo_args.append("--dev xml,qweb" if load_data_path else "--dev xml,qweb,reload")

    if bootstraped and not launch:
        return typer_retuner(ret)

    rpc_callback(ctx)  # Add RPC Options using Defaults and Envvars

    if prep_stage:
        LOGGER.info("Starting Stage Prepper Thread")
        loader_thread = threading.Thread(
            target=makedev.makedev_rpc,
            args=(ctx,),
            name="Stage Cutter",
        )
        loader_thread.start()

    if load_data_path:
        LOGGER.info("Starting Data Importer Thread")
        loader_thread = threading.Thread(
            target=rpc.folder,
            name="DataLoader",
            kwargs={"read_path": load_data_path.absolute()},
        )
        loader_thread.start()

    ret = _launch(
        odoo_path=ctx.obj.odoo_main_path,
        odoo_conf_path=ctx.obj.odoo_conf_path,
        extra_cmd_args=extra_odoo_args,
        workspace_addon_path=ctx.obj.workspace_addon_path,
        upgrade_workspace_modules=install_workspace_addons,
    )
    return typer_retuner(ret)


def launch_cli_app():
    app = typer.Typer()
    app.command()(launch_odoo)
    return app
