import typer

from .config_parameters import set_config_parameter
from .importer import import_to_odoo
from .modules import install_modules, uninstall_modules
from .translations import dump_translations


def modules_cli_app():
    app = typer.Typer(
        no_args_is_help=True,
        help="Wrapper around Odoo modules. (Install/upgrade, etc)",
    )

    app.command(name="install")(install_modules)
    app.command(name="uninstall")(uninstall_modules)
    app.command(name="dump-translation")(dump_translations)
    return app


def rpc_cli_app():
    app = typer.Typer(
        no_args_is_help=True,
        help="Functions that act on a running Odoo instance via RPC.",
    )

    app.add_typer(
        typer_instance=modules_cli_app(),
        name="modules",
    )
    app.command(name="set-config-param")(set_config_parameter)
    app.command("import")(import_to_odoo)

    return app
