"""Test CLI interface module.

This module provides the command-line interface for test operations,
including running tests, loading test data, and identifying changed modules.
"""

import typer

from .test_load_data import odoo_load_test_data
from .test_run import odoo_get_changed_modules, odoo_run_tests


def test_cli_app():
    """Create and configure the test CLI application.

    This function sets up the command-line interface for test operations,
    including commands for running tests, loading test data, and identifying
    changed modules.

    Returns:
        typer.Typer: The configured CLI application instance.
    """
    app = typer.Typer(
        no_args_is_help=True,
        help="Functions related to Odoo testing",
    )
    app.command(name="run")(odoo_run_tests)
    app.command(name="load-data")(odoo_load_test_data)
    app.command(name="get-changed-modules")(odoo_get_changed_modules)

    return app
