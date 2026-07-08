"""Shared database operation helpers for CLI commands."""

import logging

from psycopg2 import sql

from ...models import DBConnection

LOGGER = logging.getLogger(__name__)


def database_exists(connection: DBConnection, db_name: str) -> bool:
    """Return whether a database exists."""
    with connection.connect() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)", (db_name,))
        row = cur.fetchone()
        return bool(row[0]) if row else False


def terminate_connections(connection: DBConnection, db_name: str):
    """Terminate all active sessions for the given database except current backend."""
    with connection.connect() as cur:
        cur.connection.autocommit = True
        cur.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s
              AND pid <> pg_backend_pid()
            """,
            (db_name,),
        )


def drop_database(connection: DBConnection, db_name: str):
    """Terminate sessions and drop a database if it exists."""
    with connection.connect() as cur:
        cur.connection.autocommit = True
        cur.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s
              AND pid <> pg_backend_pid()
            """,
            (db_name,),
        )
        LOGGER.info("Dropping DB: %s", db_name)
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))


def create_database(connection: DBConnection, db_name: str):
    """Create an empty database."""
    with connection.connect() as cur:
        cur.connection.autocommit = True
        LOGGER.info("Creating DB: %s", db_name)
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
