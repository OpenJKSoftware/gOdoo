"""Typer adapters for database inspection."""

import logging
from pathlib import Path
from typing import Annotated

import typer

from ...database.connection import DBConnection
from ...database.state import BOOTSTRAP_EXIT_CODE, DbBootstrapStatus, classify_bootstrap_state, installed_modules
from ...runtime.locks import database_inconsistency_reason
from ..common import CommonCLI
from ..configuration import check_dangerous_command

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def _connection(
    db_name: str, db_user: str, db_host: str, db_port: int, db_password: str, *, readonly: bool = True
) -> DBConnection:
    return DBConnection(db_host, db_port, db_user, db_password, db_name, readonly=readonly)


def is_bootstrapped(
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
) -> None:
    """Report database readiness with stable exit codes."""
    connection = _connection(db_name, db_user, db_host, db_port, db_password)
    status = classify_bootstrap_state(connection)
    if database_inconsistency_reason(
        data_dir, db_name, missing_or_empty=status in (DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB)
    ):
        raise typer.Exit(BOOTSTRAP_EXIT_CODE[DbBootstrapStatus.INVALID_DB])
    raise typer.Exit(BOOTSTRAP_EXIT_CODE[status])


def get_installed_modules(
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    to_install: Annotated[bool, typer.Option("--to-install")] = False,
) -> None:
    """Print installed Odoo module names."""
    modules, status = installed_modules(
        _connection(db_name, db_user, db_host, db_port, db_password), to_install=to_install
    )
    if status is not DbBootstrapStatus.BOOTSTRAPPED:
        raise typer.Exit(BOOTSTRAP_EXIT_CODE[status])
    for module in sorted(modules):
        typer.echo(module)


def query_database(
    query: Annotated[str, typer.Argument(help="SQL query; use '-' for stdin.")],
    db_user: Annotated[str, CLI.database.db_user],
    db_name: Annotated[str, CLI.database.db_name],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    readonly: Annotated[bool, typer.Option()] = True,
) -> None:
    """Run SQL and delimit returned rows."""
    if query == "-":
        query = typer.get_text_stream("stdin").read()
    if not readonly:
        check_dangerous_command()
    try:
        with _connection(db_name, db_user, db_host, db_port, db_password, readonly=readonly).connect() as cursor:
            cursor.execute(query)
            try:
                rows = cursor.fetchall()
            except Exception:
                rows = []
        typer.echo("START QUERY_OUTPUT")
        for row in rows:
            typer.echo("\t".join(map(str, row)) if isinstance(row, tuple) else str(row))
        typer.echo("END QUERY_OUTPUT")
    except Exception as error:
        raise typer.Exit(1) from error
