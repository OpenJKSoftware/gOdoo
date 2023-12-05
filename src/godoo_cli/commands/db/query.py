import logging
from enum import Enum

import typer
from psycopg2 import OperationalError, ProgrammingError

from ...cli_common import CommonCLI
from ...helpers.cli import check_dangerous_command
from .connection import DBConnection


class DB_BOOTSTRAP_STATUS(Enum):
    BOOTSTRAPPED = 0
    NO_DB = 1
    EMPTY_DB = 2


LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


@CLI.arg_annotator
def query_database(
    query: str = typer.Argument(..., help="SQL Query"),
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
):
    """Run a Query against the database."""

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
            LOGGER.info("Running Query: %s", query)
            cursor.execute(query)
            LOGGER.info("Affected Rows: %s", cursor.rowcount)
            try:
                rows = cursor.fetchall()
                for row in rows:
                    print("\t".join(row))  # pylint: disable=print-used
            except ProgrammingError:
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
                LOGGER.warning("Database %s is empty", db_connection.db_name)
                return DB_BOOTSTRAP_STATUS.EMPTY_DB
            LOGGER.debug("Database %s is not empty", db_connection.db_name)
            return DB_BOOTSTRAP_STATUS.BOOTSTRAPPED
    except OperationalError:
        LOGGER.warning("Database %s does not exist", db_connection.db_name)
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
    check_dangerous_command()
    connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name=db_name,
    )
    boot = _is_bootstrapped(db_connection=connection)
    raise typer.Exit(boot.value)


def _get_installed_modules(db_connection: DBConnection):
    """Get list of installed modules in database"""
    if boot := _is_bootstrapped(db_connection=db_connection) != DB_BOOTSTRAP_STATUS.BOOTSTRAPPED:
        return boot.value
    with db_connection.connect() as cursor:
        cursor.execute("SELECT name FROM ir_module_module WHERE state = 'installed';")
        sql_res = cursor.fetchall()
        return [r[0] for r in sql_res]


@CLI.arg_annotator
def get_installed_modules(
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
):
    """Returns Modules Marked as installed by Odoo in the database"""
    db_connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name=db_name,
    )
    installed_modules = _get_installed_modules(db_connection=db_connection)
    if isinstance(installed_modules, int):
        raise typer.Exit(installed_modules)
    for module in sorted(installed_modules):
        print(module)  # pylint: disable=print-used
