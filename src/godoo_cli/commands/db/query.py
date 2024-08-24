import logging
from enum import Enum

import typer
from psycopg2 import OperationalError, ProgrammingError

from ...cli_common import CommonCLI
from ...helpers.cli import check_dangerous_command
from .connection import DBConnection


class DB_BOOTSTRAP_STATUS(Enum):
    BOOTSTRAPPED = "bootstrapped"
    NO_DB = "db missing"
    EMPTY_DB = "db empty"


LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


@CLI.arg_annotator
def query_database(
    query: str = typer.Argument(..., help="SQL Query. Use '-' to read from stdin."),
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
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
        except Exception as e:
            LOGGER.exception(e)
            raise typer.Exit(1)


def _is_bootstrapped(db_connection: DBConnection) -> DB_BOOTSTRAP_STATUS:
    """check if postgres contains database db_name and if this database has any tables present"""
    try:
        with db_connection.connect() as cursor:
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public');")
            if not cursor.fetchone()[0]:
                LOGGER.debug("Database '%s' is empty", db_connection.db_name)
                return DB_BOOTSTRAP_STATUS.EMPTY_DB
            LOGGER.debug("Database '%s' is not empty", db_connection.db_name)
            return DB_BOOTSTRAP_STATUS.BOOTSTRAPPED
    except OperationalError:
        LOGGER.debug("Database '%s' does not exist", db_connection.db_name)
        return DB_BOOTSTRAP_STATUS.NO_DB


@CLI.arg_annotator
def is_bootstrapped(
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
):
    """Check if the database is empty. Return code  = 1 if database does not exist, 2 if database is empty, 0 if database is not empty."""
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
        DB_BOOTSTRAP_STATUS.BOOTSTRAPPED: 0,
        DB_BOOTSTRAP_STATUS.NO_DB: 20,
        DB_BOOTSTRAP_STATUS.EMPTY_DB: 21,
    }
    raise typer.Exit(ret_mapping[bootstrap_value])


def _get_installed_modules(db_connection: DBConnection, to_install=False):
    """Get list of installed modules in database (to_install includes the modules marked for installation)"""
    if (boot := _is_bootstrapped(db_connection=db_connection)) != DB_BOOTSTRAP_STATUS.BOOTSTRAPPED:
        return boot.value
    lookup_states = ["installed", "to upgrade"]
    if to_install:
        lookup_states.append("to install")
    with db_connection.connect() as cursor:
        cursor.execute("SELECT name FROM ir_module_module WHERE state IN %s;", [tuple(lookup_states)])
        sql_res = cursor.fetchall()
        return [r[0] for r in sql_res]


@CLI.arg_annotator
def get_installed_modules(
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
    to_install: bool = typer.Option(
        False,
        "--to-install",
        help="Include modules marked for installation",
    ),
):
    """Returns Modules Marked as installed by Odoo in the database"""
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
