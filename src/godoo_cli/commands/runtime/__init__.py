"""Runtime-oriented CLI command groups."""

import logging

import typer

from .init import deployment_init_odoo_runtime
from .launch import launch_odoo
from .shell import odoo_shell, odoo_shell_run_script
from .status import runtime_status

LOGGER = logging.getLogger(__name__)


def runtime_cli_app() -> typer.Typer:
    """Build the canonical runtime command group."""
    app = typer.Typer(
        no_args_is_help=True,
        help="Manage an Odoo runtime: its lifecycle, database, and matching filestore.",
    )
    app.command("init")(deployment_init_odoo_runtime)
    app.command("launch")(launch_odoo)
    app.command("status")(runtime_status)
    app.command("shell")(odoo_shell)
    app.command("shell-script")(odoo_shell_run_script)
    return app
