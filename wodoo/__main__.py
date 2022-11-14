"""Main CLI for the Wodoo Odoo Wrapper, Wodoo."""
import sys
from pathlib import Path
from types import SimpleNamespace

import typer

from . import bootstrap, launch, makedev, rpc, shell, test, update
from .helper import set_logging

sys.path.append(Path(__file__).parent)
from dotenv import load_dotenv

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

def main():
    load_dotenv(".env", override=True)

    app = typer.Typer(callback=main_callback)
    # Nested Subcommands
    app.add_typer(rpc._app, name="rpc", help="Various RPC Wrappers")
    app.add_typer(makedev._app, name="makedev", help="Set config and RPC settings to generate a staging environment")

    # Normal Subcommands
    app.command("launch", help="Launch Odoo, Bootstrap if bootstrapflag is not present")(launch.launch)
    app.command("bootstrap", help="Bootstrap Odoo")(bootstrap.bootstrap)
    app.command("update", help="Update Odoo, Thirdparty addons and Zip Addons ")(update.update_addons)
    app.command("test", help="Bootstrap or Launch odoo in Testing Mode")(test.test)
    app.command("shell", help="Shell into Odoo")(shell.shell)
    app.command("uninstall", help="Uninstall Modules")(shell.uninstall_modules)
    app()


if __name__ == "__main__":
    main()
