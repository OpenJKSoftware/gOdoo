"""Configure the gOdoo command-line interface."""

import logging
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich import print as rich_print

from .. import __about__
from ..helpers.system import set_logging
from . import db_cli_app, rpc_cli_app, run_odoo, runtime_cli_app, test_cli_app, upgrade, workspace_cli_app

LOGGER = logging.getLogger(__name__)


def print_version() -> None:
    """Print the installed gOdoo version."""
    rich_print(f"gOdoo Version: [bold green]{__about__.__version__}[/bold green]")


def main_callback(
    verbose: Annotated[
        bool | None,
        typer.Option(
            "--verbose",
            "-v",
            envvar="GODOO_VERBOSE",
            help="Verbose Logging with Error stacktraces",
        ),
    ] = False,
    log_filter: Annotated[
        str | None,
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
        verbose=bool(verbose),
        log_filter=log_filter,
    )


def main_cli():
    """Build the CLI with .env defaults while preserving the process environment."""
    load_dotenv(".env", override=False)

    help_text = "gOdoo CLI for Interacting with Odoo"
    app = typer.Typer(
        no_args_is_help=True,
        callback=main_callback,
        rich_markup_mode="rich",
        help=help_text,
    )

    # Public command groups
    app.add_typer(
        typer_instance=workspace_cli_app(),
        name="workspace",
    )
    app.add_typer(
        typer_instance=runtime_cli_app(),
        name="runtime",
    )
    app.add_typer(
        typer_instance=db_cli_app(),
        name="db",
    )
    app.add_typer(
        typer_instance=rpc_cli_app(),
        name="rpc",
    )
    app.add_typer(
        typer_instance=test_cli_app(),
        name="test",
    )

    # Public commands
    app.command("version")(print_version)
    app.command(
        "run",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )(run_odoo)
    app.command(
        "upgrade",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )(upgrade)
    return app


def launch_cli():
    """Build and run the gOdoo CLI application."""
    app = main_cli()
    app()
