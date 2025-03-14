"""Database query functionality module.

This module provides functionality for querying Odoo databases,
including checking bootstrap status and retrieving installed modules.
"""

import enum
import logging
from typing import Annotated

import typer
from psycopg2 import OperationalError, ProgrammingError

from ...cli_common import CommonCLI
from ...helpers.cli import check_dangerous_command
from .connection import DBConnection


class DbBootstrapStatus(enum.Enum):
    """Database bootstrap status enumeration.

    This enum represents the possible states of database bootstrapping:
    - BOOTSTRAPPED: Database is fully bootstrapped
    - NO_DB: Database does not exist
    - EMPTY_DB: Database exists but is empty
    """

    BOOTSTRAPPED = "bootstrapped"
    NO_DB = "db missing"
    EMPTY_DB = "db empty"


LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def query_database(
    query: Annotated[str, typer.Argument(help="SQL Query. Use '-' to read from stdin.")],
    db_user: Annotated[str, CLI.database.db_user],
    db_name: Annotated[str, CLI.database.db_name],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
):
    """Run a Query against the database.

    Queries, with return values will be printed to stdout between "START QUERY_OUTPUT" and "END QUERY_OUTPUT".
    """
    # read stdin if query is not provided
    if query == "-":
        stdin = typer.get_text_stream("stdin")
        query = stdin.read()

    check_dangerous_command()
    # regex to check if SQL query contains writing command

    connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name=db_name,
    )
    with connection.connect() as cursor:
        try:
            LOGGER.info("Running Query against Odoo DB: %s", query)
            cursor.execute(query)
            LOGGER.info("Affected Rows: %s", cursor.rowcount)
            try:
                rows = cursor.fetchall()
                # Use Print here to write to Stdout.
                # START and END query are there to help parsing the actual output
                print("START QUERY_OUTPUT")  # pylint: disable=print-used
                for row in rows:
                    print_line = "\t".join(map(str, row)) if isinstance(row, tuple) else str(row)
                    print(print_line)  # pylint: disable=print-used
                print("END QUERY_OUTPUT")  # pylint: disable=print-used
            except ProgrammingError:
                # When there is nothing to fetch, fetchall() raises a ProgrammingError
                pass
        except Exception:
            raise typer.Exit(1)  # noqa: B904


def _is_bootstrapped(db_connection: DBConnection) -> DbBootstrapStatus:
    """Check if postgres contains database db_name and if this database has any tables present."""
    try:
        with db_connection.connect() as cursor:
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public');")
            if not cursor.fetchone()[0]:
                LOGGER.debug("Database '%s' is empty", db_connection.db_name)
                return DbBootstrapStatus.EMPTY_DB
            LOGGER.debug("Database '%s' is not empty", db_connection.db_name)
            return DbBootstrapStatus.BOOTSTRAPPED
    except OperationalError:
        LOGGER.debug("Database '%s' does not exist", db_connection.db_name)
        return DbBootstrapStatus.NO_DB


def is_bootstrapped(
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
):
    """Check if the database is empty.

    Return code = 1 if database does not exist, 2 if database is empty, 0 if database is not empty.
    """
    connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name=db_name,
    )
    bootstrap_value = _is_bootstrapped(db_connection=connection)
    LOGGER.info("Odoo Database Status: %s", bootstrap_value.value)
    ret_mapping = {
        DbBootstrapStatus.BOOTSTRAPPED: 0,
        DbBootstrapStatus.NO_DB: 20,
        DbBootstrapStatus.EMPTY_DB: 21,
    }
    raise typer.Exit(ret_mapping[bootstrap_value])


def _get_installed_modules(db_connection: DBConnection, to_install: bool = False) -> list[str]:
    """Get list of installed modules in database (to_install includes the modules marked for installation)."""
    if (boot := _is_bootstrapped(db_connection=db_connection)) != DbBootstrapStatus.BOOTSTRAPPED:
        return boot.value
    lookup_states = ["installed", "to upgrade"]
    if to_install:
        lookup_states.append("to install")
    with db_connection.connect() as cursor:
        cursor.execute(
            "SELECT name FROM ir_module_module WHERE state IN %s;",
            [tuple(lookup_states)],
        )
        sql_res = cursor.fetchall()
        return [r[0] for r in sql_res]


def get_installed_modules(
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    to_install: Annotated[
        bool,
        typer.Option(
            "--to-install",
            help="Include modules marked for installation",
        ),
    ] = False,
):
    """Returns modules marked as installed by Odoo in the database."""
    db_connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name=db_name,
    )
    installed_modules = _get_installed_modules(db_connection=db_connection, to_install=to_install)
    if isinstance(installed_modules, int):
        raise typer.Exit(installed_modules)
    for module in sorted(installed_modules):
        print(module)  # pylint: disable=print-used
