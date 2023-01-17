"""Main CLI."""
from typing import Optional

import typer
from dotenv import load_dotenv
from rich import print as rich_print

from .cli_common import CommonCLI
from .commands import (
    bootstrap_odoo,
    db_cli_app,
    launch_import,
    launch_odoo,
    odoo_shell,
    odoo_test,
    rpc_cli_app,
    set_odoo_config,
    source_cli_app,
    uninstall_modules,
)
from .helpers.odoo_files import odoo_bin_get_version
from .helpers.system import set_logging
from .version import __version__

CLI = CommonCLI()


def main_callback(
    verbose: Optional[bool] = typer.Option(False, "--verbose", "-v", help="Verbose Logging with Error stacktraces")
):
    set_logging(verbose=verbose)


@CLI.arg_annotator
def print_versions(
    odoo_main_path=CLI.odoo_paths.bin_path,
):
    """
    Print gOdoo and Odoo Version info.
    """
    rich_print(f"gOdoo Version: [bold green]{__version__}[/bold green]")
    odoo_version = odoo_bin_get_version(odoo_main_path)
    rich_print(f"Odoo Version: [bold green]{odoo_version}[/bold green]")


def main_cli():
    load_dotenv(".env", override=True)

    help_text = "gOdoo CLI for Interacting with Odoo"
    app = typer.Typer(no_args_is_help=True, callback=main_callback, rich_markup_mode="rich", help=help_text)
    # Nested Subcommands
    app.add_typer(
        typer_instance=rpc_cli_app(),
        name="rpc",
    )
    app.add_typer(
        typer_instance=db_cli_app(),
        name="db",
    )
    app.add_typer(
        typer_instance=source_cli_app(),
        name="source",
    )

    # Normal Subcommands
    app.command("version")(print_versions)
    app.command("bootstrap")(bootstrap_odoo)
    app.command("launch")(launch_odoo)
    app.command("launch-import")(launch_import)
    app.command("test")(odoo_test)
    app.command("config")(set_odoo_config)
    app.command("shell")(odoo_shell)
    app.command("uninstall")(uninstall_modules)
    return app


def launch_cli():
    app = main_cli()
    app()
