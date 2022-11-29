"""Main CLI."""
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import typer
from dotenv import load_dotenv

from .commands import (
    bootstrap_odoo,
    get_source,
    get_source_file,
    install_module_dependencies,
    launch_odoo,
    odoo_shell,
    odoo_test,
    rpc_cli_app,
    set_odoo_config,
    uninstall_modules,
)
from .helpers.system import set_logging
from .version import __version__


def main_callback(
    ctx: typer.Context,
    odoo_main_path: Path = typer.Option(
        ...,
        envvar="ODOO_MAIN_FOLDER",
        help="folder with odoo-bin",
        rich_help_panel="Paths",
    ),
    odoo_conf_path: Path = typer.Option(
        ...,
        envvar="ODOO_CONF_PATH",
        help="odoo.conf path",
        rich_help_panel="Paths",
    ),
    workspace_addon_path: Path = typer.Option(
        ...,
        envvar="ODOO_WORKSPACE_ADDON_LOCATION",
        help="path to dev workspace addons",
        rich_help_panel="Paths",
    ),
    bootstrap_flag_location: Path = typer.Option(
        ...,
        envvar="ODOO_BOOTSTRAP_FLAG",
        help="Location of the Bootstrap indicator file",
        rich_help_panel="Paths",
    ),
    source_download_archive: Optional[bool] = typer.Option(
        False,
        "--source-download-archive",
        envvar="SOURCE_CLONE_ARCHIVE",
        help="When using a HTTPs Repo Url for Github we can download a snapshop without the Repo history",
    ),
    verbose: Optional[bool] = typer.Option(False, "--verbose", help="Verbose Logging with Error stacktraces"),
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

    help_text = f"gOdoo CLI version: [bold green]{__version__}[/bold green]"
    app = typer.Typer(callback=main_callback, no_args_is_help=True, rich_markup_mode="rich", help=help_text)
    # Nested Subcommands
    app.add_typer(
        typer_instance=rpc_cli_app(),
        name="rpc",
    )

    # Normal Subcommands
    app.command("launch")(launch_odoo)
    app.command("bootstrap")(bootstrap_odoo)
    app.command("test")(odoo_test)
    app.command("config")(set_odoo_config)
    app.command("source-get-file")(get_source_file)
    app.command("source-get-depends")(install_module_dependencies)
    app.command("source-get")(get_source)
    app.command("shell")(odoo_shell)
    app.command("uninstall")(uninstall_modules)
    return app


def launch_cli():
    app = main_cli()
    app()
