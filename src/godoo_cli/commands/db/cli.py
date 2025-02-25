"""Database management CLI module.

This module provides the command-line interface for database management operations,
including password management, database queries, and bootstrap status checks.
"""

import typer

from .connection import login_db
from .passwords import set_passwords
from .query import get_installed_modules, is_bootstrapped, query_database


def db_cli_app():
    """Create and configure the database CLI application.

    This function sets up the command-line interface for database operations,
    including commands for password management, database queries, and status checks.

    Returns:
        typer.Typer: The configured CLI application instance.
    """
    app = typer.Typer(
        no_args_is_help=True,
        help="Functions that directly act on the Postgres DB",
    )

    app.command()(set_passwords)
    app.command("login")(login_db)
    app.command("query")(query_database)
    app.command("odoo-bootstrapped")(is_bootstrapped)
    app.command("installed-modules")(get_installed_modules)

    return app
