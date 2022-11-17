import typer

from .cli_callbacks import rpc_callback
from .importer import import_to_odoo
from .modules import modules_cli_app
from .translations import dump_translations


def rpc_cli_app():
    app = typer.Typer(no_args_is_help=True, callback=rpc_callback)

    app.add_typer(
        typer_instance=modules_cli_app(), name="modules", help="Wrapper around Odoo modules. (Install/upgrade, etc)"
    )
    app.command("import_folder", help="Imports all files in a Folder according to a regex")(import_to_odoo)
    app.command(help="Upgrades or Installs Modules in Odoo via RPC.")
    app.command(help="Upgrades Addons and Exports Translation .pot file")(dump_translations)

    return app
