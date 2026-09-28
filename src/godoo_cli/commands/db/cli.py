"""Register the database command group."""

import logging
import subprocess
from typing import Annotated

import typer

from ...database.connection import DBConnection
from ...database.postgres import postgres_argv, postgres_environment
from ..common import CommonCLI
from .archive import dump_database, load_database
from .cow import duplicate_cow
from .passwords import set_passwords
from .prepare import prepare_database
from .query import get_installed_modules, is_bootstrapped, query_database
from .reset import reset_odoo_state

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def login_db(
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
) -> None:
    """Open an interactive PostgreSQL session."""
    connection = DBConnection(db_host, db_port, db_user, db_password, db_name)
    with postgres_environment(connection) as environment:
        subprocess.run(["psql", *postgres_argv(connection)], env=environment, check=False)


def db_cli_app() -> typer.Typer:
    """Build the database command group."""
    app = typer.Typer(no_args_is_help=True, help="Functions that directly act on PostgreSQL databases.")
    app.command("prepare")(prepare_database)
    app.command("backup")(dump_database)
    app.command("restore")(load_database)
    app.command("clone")(duplicate_cow)
    app.command("reset")(reset_odoo_state)
    app.command("status")(is_bootstrapped)
    app.command()(set_passwords)
    app.command("login")(login_db)
    app.command("query")(query_database)
    app.command("installed-modules")(get_installed_modules)
    return app
