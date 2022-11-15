"""Main CLI for the Wodoo Odoo Wrapper, Wodoo."""
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import typer
from dotenv import load_dotenv
from rich.logging import RichHandler

from .commands import (
    bootstrap_odoo,
    launch_odoo,
    makedec_cli_app,
    odoo_shell,
    odoo_test,
    rpc_cli_app,
    uninstall_modules,
    update_addons,
)
from .helpers import set_logging


def main_callback(
    ctx: typer.Context,
    odoo_main_path: Path = typer.Option(..., envvar="ODOO_MAIN_FOLDER", help="folder with odoo-bin"),
    odoo_conf_path: Path = typer.Option(..., envvar="ODOO_CONF_PATH", help="odoo.conf path"),
    workspace_addon_path: Path = typer.Option(
        ..., envvar="ODOO_WORKSPACE_ADDON_LOCATION", help="path to dev workspace addons"
    ),
    bootstrap_flag_location: Path = typer.Option(
        ..., envvar="ODOO_BOOTSTRAP_FLAG", help="Location of the Bootstrap indicator file"
    ),
    source_download_archive: bool = typer.Option(
        False,
        envvar="SOURCE_CLONE_ARCHIVE",
        help="When using a HTTPs Repo Url for Github we can download a snapshop without the Repo history",
    ),
    verbose: bool = typer.Option(False),
):
    ctx.obj = SimpleNamespace(
        odoo_main_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        bootstrap_flag_location=bootstrap_flag_location,
        source_download_archive=source_download_archive,
    )
    set_logging(verbose=verbose)


def main_cli():
    load_dotenv(".env", override=True)

    app = typer.Typer(callback=main_callback)
    # Nested Subcommands
    app.add_typer(
        typer_instance=rpc_cli_app(),
        name="rpc",
        help="Various RPC Wrappers",
    )
    app.add_typer(
        typer_instance=makedec_cli_app(),
        name="makedev",
        help="Set config and RPC settings to generate a staging environment",
    )

    # Normal Subcommands
    app.command("launch", help="Launch Odoo, Bootstrap if bootstrapflag is not present")(launch_odoo)
    app.command("bootstrap", help="Bootstrap Odoo")(bootstrap_odoo)
    app.command("update", help="Update Odoo, Thirdparty addons and Zip Addons ")(update_addons)
    app.command("test", help="Bootstrap or Launch odoo in Testing Mode")(odoo_test)
    app.command("shell", help="Shell into Odoo")(odoo_shell)
    app.command("uninstall", help="Uninstall Modules")(uninstall_modules)
    return app


def launch_cli():
    app = main_cli()
    app()
