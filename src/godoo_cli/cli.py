"""Configure the gOdoo command-line interface."""

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
    log_filter: Annotated[
        Optional[str],
        typer.Option(
            "--log-filter",
            "-lf",
            envvar="GODOO_LOG_FILTER",
            help="Regex pattern to filter log output by logger name (e.g. 'odoo.test.suite')",
        ),
    ] = None,
):
    """Configure logging before running a gOdoo command."""
    set_logging(
        verbose=bool(verbose) if verbose is not None else False,
        log_filter=log_filter,
    )


def main_cli():
    """Build the gOdoo CLI application."""
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
    app.add_typer(
        typer_instance=cmd.runtime_cli_app(),
        name="runtime",
    )
    app.add_typer(typer_instance=cmd.backup_cli_app(), name="backup")
    app.add_typer(typer_instance=cmd.test_cli_app(), name="test")

    # Normal Subcommands
    app.command("version")(print_versions)
    app.command("dev")(cmd.dev_odoo)
    app.command("ensure-runtime")(cmd.ensure_odoo_runtime)
    app.command("reconcile-runtime")(cmd.reconcile_odoo_runtime)
    app.command("deployment-init")(cmd.deployment_init_odoo_runtime)
    app.command("prepare")(cmd.prepare_odoo)
    app.command("bootstrap")(cmd.bootstrap_odoo)
    app.command("launch")(cmd.launch_odoo)
    app.command("launch-import")(cmd.launch_import)
    app.command("reset")(cmd.reset_odoo_state)
    app.command("config")(cmd.set_odoo_config)
    app.command("shell")(cmd.odoo_shell)
    app.command("shell-script")(cmd.odoo_shell_run_script)
    app.command("uninstall")(cmd.odoo_shell_uninstall_modules)
    return app


def launch_cli():
    """Build and run the gOdoo CLI application."""
    app = main_cli()
    app()
