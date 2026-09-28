"""Define Odoo test commands."""

import typer

from .load_data import odoo_load_test_data
from .run import odoo_get_changed_modules, odoo_run_tests


def test_cli_app():
    """Build the Odoo test command group."""
    app = typer.Typer(
        no_args_is_help=True,
        help="Functions related to Odoo testing",
    )
    app.command(name="run")(odoo_run_tests)
    app.command(name="load-data")(odoo_load_test_data)
    app.command(name="get-changed-modules")(odoo_get_changed_modules)

    return app
