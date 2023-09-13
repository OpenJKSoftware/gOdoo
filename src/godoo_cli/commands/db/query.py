import logging

import typer

from ...cli_common import CommonCLI
from ...helpers.cli import check_dangerous_command
from .connection import DBConnection

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
            cursor.execute(query)
            rows = cursor.fetchall()
            for row in rows:
                print("\t".join(row))  # pylint: disable=print-used
        except Exception:
            raise typer.Exit(1)
