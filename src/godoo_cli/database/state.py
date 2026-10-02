"""Database bootstrap classification and module inspection."""

import enum
import logging
import re

from psycopg2 import Error, OperationalError

from .connection import DBConnection

LOGGER = logging.getLogger(__name__)


def base_module_major(connection: DBConnection) -> int:
    """Return the installed Odoo base module major version."""
    try:
        with connection.connect() as cursor:
            cursor.execute("SELECT latest_version FROM ir_module_module WHERE name = 'base';")
            row = cursor.fetchone()
    except Error as error:
        message = "Could not read the base module version from the Odoo database."
        raise RuntimeError(message) from error

    version = row[0] if row else None
    if not isinstance(version, str) or re.fullmatch(r"\d+(?:\.\d+)*", version) is None:
        message = "The Odoo database has no usable base module version."
        raise RuntimeError(message)
    return int(version.split(".", maxsplit=1)[0])


class DbBootstrapStatus(enum.Enum):
    """Describe whether a database can be used as an Odoo runtime."""

    BOOTSTRAPPED = "bootstrapped"
    NO_DB = "db missing"
    EMPTY_DB = "db empty"
    INVALID_DB = "db invalid"


BOOTSTRAP_EXIT_CODE = {
    DbBootstrapStatus.BOOTSTRAPPED: 0,
    DbBootstrapStatus.NO_DB: 20,
    DbBootstrapStatus.EMPTY_DB: 21,
    DbBootstrapStatus.INVALID_DB: 22,
}


def classify_bootstrap_state(connection: DBConnection) -> DbBootstrapStatus:
    """Classify a database as missing, empty, bootstrapped, or invalid."""
    try:
        with connection.connect() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public'), to_regclass('public.ir_module_module') IS NOT NULL;"
            )
            row = cursor.fetchone()
            if row is None or not row[0]:
                return DbBootstrapStatus.EMPTY_DB
            if not row[1]:
                return DbBootstrapStatus.INVALID_DB
            cursor.execute("SELECT state FROM ir_module_module WHERE name = 'base';")
            base = cursor.fetchone()
            return (
                DbBootstrapStatus.BOOTSTRAPPED
                if base is not None and base[0] in {"installed", "to upgrade"}
                else DbBootstrapStatus.INVALID_DB
            )
    except OperationalError as target_error:
        try:
            with connection.with_db("postgres", readonly=True).connect() as cursor:
                cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s);", [connection.db_name])
                row = cursor.fetchone()
        except OperationalError as maintenance_error:
            raise target_error from maintenance_error
        if row is None or row[0]:
            raise target_error
        return DbBootstrapStatus.NO_DB


def installed_modules(connection: DBConnection, *, to_install: bool = False) -> tuple[list[str], DbBootstrapStatus]:
    """Return installed modules and optionally pending installation modules."""
    if (status := classify_bootstrap_state(connection)) is not DbBootstrapStatus.BOOTSTRAPPED:
        return [], status
    states = ["installed", "to upgrade"] + (["to install"] if to_install else [])
    with connection.connect() as cursor:
        cursor.execute("SELECT name FROM ir_module_module WHERE state IN %s;", [tuple(states)])
        return [row[0] for row in cursor.fetchall()], status


def database_exists(connection: DBConnection, db_name: str) -> bool:
    """Return whether PostgreSQL has a database with this name."""
    with connection.connect() as cursor:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)", (db_name,))
        row = cursor.fetchone()
        return bool(row[0]) if row else False
