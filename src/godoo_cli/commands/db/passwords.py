import logging

import typer
from passlib.context import CryptContext

from ...cli_common import CommonCLI
from ...helpers.cli import check_dangerous_command
from .connection import DBConnection

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def _hash_odoo_password(password: str) -> str:
    """Hash Password for Odoo

    Parameters
    ----------
    password : str
        Password to hash

    Returns
    -------
    str
        Hashed Password
    """
    return CryptContext(schemes=["pbkdf2_sha512", "md5_crypt"]).encrypt(password)


@CLI.arg_annotator
def set_passwords(
    new_password: str = typer.Argument(..., help="Password to set for all users"),
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
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
    with connection.connect() as conn:
        conn.execute(f"UPDATE res_users SET password='{hashed_pw}'")
    LOGGER.info("Password for all users set to: '%s'", new_password)
