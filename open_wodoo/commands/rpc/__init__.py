"""
Things that interact with Odoo RPC.
Must Provide username and Password
"""
import typer

from .cli import rpc_callback
from .importer import wodoo_import_folder
from .modules import modules_cli_app
from .translations import dump_translations


def rpc_cli_app():
    app = typer.Typer(callback=rpc_callback)

    app.add_typer(
        typer_instance=modules_cli_app(), name="modules", help="Wrapper around Odoo modules. (Install/upgrade, etc)"
    )
    app.command("import_folder", help="Imports all files in a Folder according to a regex")(wodoo_import_folder)
    app.command(help="Upgrades or Installs Modules in Odoo via RPC.")
    app.command(help="Upgrades Addons and Exports Translation .pot file")(dump_translations)

    return app
