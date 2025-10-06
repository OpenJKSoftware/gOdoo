"""Main CLI module for gOdoo.

This module serves as the main entry point for the gOdoo CLI application.
It sets up the command structure, configures logging, and provides the core CLI functionality
for interacting with Odoo instances.
"""

from pathlib import Path
from typing import Annotated, Optional

import typer
from dotenv import load_dotenv
from rich import print as rich_print

from . import __about__
from . import commands as cmd
from .cli_common import CommonCLI
from .helpers.odoo_files import odoo_bin_get_version
from .helpers.system import set_logging

CLI = CommonCLI()


def print_versions(odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path]):
    """Print gOdoo and Odoo Version info."""
    rich_print(f"gOdoo Version: [bold green]{__about__.__version__}[/bold green]")
    odoo_version = odoo_bin_get_version(odoo_main_path)
    rich_print(f"Odoo Version: [bold green]{odoo_version.raw}[/bold green]")


def main_callback(
    verbose: Annotated[
        Optional[bool],
        typer.Option(
            "--verbose",
            "-v",
            envvar="GODOO_VERBOSE",
            help="Verbose Logging with Error stacktraces",
        ),
    ] = False,
):
    """Configure the CLI's logging level.

    Args:
        verbose: If True, enables verbose logging with error stacktraces.
            Can be set via --verbose flag or GODOO_VERBOSE environment variable.
    """
    set_logging(verbose=bool(verbose) if verbose is not None else False)


def main_cli():
    """Initialize and configure the main CLI application.

    This function sets up the CLI structure with all available commands and subcommands.
    It loads environment variables from .env file and configures the help text and callbacks.

    Returns:
        typer.Typer: The configured CLI application instance.
    """
    load_dotenv(".env", override=True)

    help_text = "gOdoo CLI for Interacting with Odoo"
    app = typer.Typer(
        no_args_is_help=True,
        callback=main_callback,
        rich_markup_mode="rich",
        help=help_text,
    )

    # Nested Subcommands
    app.add_typer(
        typer_instance=cmd.rpc_cli_app(),
        name="rpc",
    )
    app.add_typer(
        typer_instance=cmd.db_cli_app(),
        name="db",
    )
    app.add_typer(
        typer_instance=cmd.source_cli_app(),
        name="source",
    )
    app.add_typer(typer_instance=cmd.backup_cli_app(), name="backup")
    app.add_typer(typer_instance=cmd.test_cli_app(), name="test")

    # Normal Subcommands
    app.command("version")(print_versions)
    app.command("bootstrap")(cmd.bootstrap_odoo)
    app.command("launch")(cmd.launch_odoo)
    app.command("launch-import")(cmd.launch_import)
    app.command("test")(cmd.odoo_run_tests)
    app.command("config")(cmd.set_odoo_config)
    app.command("shell")(cmd.odoo_shell)
    app.command("shell-script")(cmd.odoo_shell_run_script)
    app.command("uninstall")(cmd.odoo_shell_uninstall_modules)
    return app


def launch_cli():
    """Launch the gOdoo CLI application.

    This is the main entry point for the CLI application.
    It creates and runs the CLI app with all configured commands.
    """
    app = main_cli()
    app()
