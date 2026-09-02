"""Runtime-oriented CLI command groups."""

import logging

import typer

from ..db.query import is_bootstrapped
from ..lifecycle import bootstrap_odoo_runtime, deployment_init_odoo_runtime, reconcile_odoo_runtime
from ..odoo_bin.launch import launch_odoo, prepare_odoo
from .storage import runtime_storage_cli_app

LOGGER = logging.getLogger(__name__)


def runtime_cli_app() -> typer.Typer:
    """Create the canonical Odoo runtime lifecycle command group.

    Lifecycle commands reuse their established top-level implementations. The
    legacy top-level commands remain registered as compatibility aliases.
    """
    app = typer.Typer(
        no_args_is_help=True,
        help="Manage an Odoo runtime: its lifecycle, database, and matching filestore.",
    )
    app.command("prepare")(prepare_odoo)
    app.command("bootstrap")(bootstrap_odoo_runtime)
    app.command("reconcile")(reconcile_odoo_runtime)
    app.command("init")(deployment_init_odoo_runtime)
    app.command("launch")(launch_odoo)
    app.command("status")(is_bootstrapped)
    app.add_typer(runtime_storage_cli_app(), name="storage")
    return app
