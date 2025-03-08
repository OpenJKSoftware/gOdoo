"""Database password management module.

This module provides functionality for managing Odoo database passwords,
including setting and retrieving admin passwords.
"""

import logging
from typing import Annotated

import typer
from passlib.context import CryptContext

from ...cli_common import CommonCLI
from ...helpers.cli import check_dangerous_command
from .connection import DBConnection

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def _hash_odoo_password(password: str) -> str:
    """Hash Password for Odoo.

    Parameters
    ----------
    password : str
        Password to hash

    Returns:
    -------
    str
        Hashed Password
    """
    return CryptContext(schemes=["pbkdf2_sha512", "md5_crypt"]).encrypt(password)


def set_passwords(
    new_password: Annotated[str, typer.Argument(help="Password to set for all users")],
    db_user: Annotated[str, CLI.database.db_user],
    db_name: Annotated[str, CLI.database.db_name],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
):
    """Set Login Password for all Users."""
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
