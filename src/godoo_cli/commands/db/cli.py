"""Database management CLI module.

This module provides the command-line interface for database management operations,
including password management, database queries, and bootstrap status checks.
"""

import subprocess
from typing import Annotated

import typer

from ...cli_common import CommonCLI
from .passwords import set_passwords
from .query import get_installed_modules, is_bootstrapped, query_database

CLI = CommonCLI()


def login_db(
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
):
    """Launch an interactive psql CLI session with the provided credentials.

    This function starts an interactive PostgreSQL command-line session using
    the provided database connection parameters.

    Args:
        db_host: Database host address.
        db_port: Database port number.
        db_name: Name of the database to connect to.
        db_user: Database username.
        db_password: Database password.
    """
    command = ["psql", f"-h{db_host}", f"-U{db_user}", f"-d{db_name}"]
    if db_port != 0:
        command.append(f"-p{db_port}")
    subprocess.run(command, env={"PGPASSWORD": db_password})


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
