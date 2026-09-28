"""Manage Odoo user passwords."""

import logging
from typing import Annotated

import typer
from passlib.context import CryptContext

from ...database.connection import DBConnection
from ..common import CommonCLI
from ..configuration import check_dangerous_command

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def _hash_odoo_password(password: str) -> str:
    """Hash a password in Odoo's expected format."""
    return CryptContext(schemes=["pbkdf2_sha512", "md5_crypt"]).encrypt(password)


def set_passwords(
    new_password: Annotated[str, typer.Argument(help="Password to set for all users")],
    db_user: Annotated[str, CLI.database.db_user],
    db_name: Annotated[str, CLI.database.db_name],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
):
    """Set the login password for every Odoo user."""
    check_dangerous_command()

    connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name=db_name,
    )
    hashed_pw = _hash_odoo_password(new_password)
    with connection.connect() as cur:
        try:
            cur.execute(f"UPDATE res_users SET password='{hashed_pw}'")
        except Exception:
            LOGGER.exception("Error setting password for all users")
            raise typer.Exit(1)  # noqa: B904
    LOGGER.info("Password for all users set to: '%s'", new_password)
