"""Database cloning helper functions."""

import logging

from psycopg2 import sql

from ...models import DBConnection
from .ops import create_database, database_exists, drop_database, terminate_connections

LOGGER = logging.getLogger(__name__)


def create_database_from_template(
    connection: DBConnection,
    template_db_name: str,
    target_db_name: str,
    recreate_target: bool = False,
    allow_fallback_create: bool = False,
    use_file_copy_strategy: bool = False,
) -> bool:
    """Create target database from a template.

    Returns:
        bool: True when created from template, False when fallback empty DB creation was used.

    Raises:
        ValueError: If target equals template or template DB is missing while fallback is disabled.
    """
    if template_db_name == target_db_name:
        msg = "Template and target database names must differ"
        LOGGER.error(msg)
        raise ValueError(msg)

    if recreate_target and database_exists(connection, target_db_name):
        drop_database(connection, target_db_name)

    if database_exists(connection, target_db_name):
        LOGGER.info("Clone DB already exists, skipping create: %s", target_db_name)
        return True

    if not database_exists(connection, template_db_name):
        if not allow_fallback_create:
            msg = f"Template DB not found: {template_db_name}"
            LOGGER.error(msg)
            raise ValueError(msg)
        LOGGER.warning("Template DB '%s' missing. Creating empty DB '%s'", template_db_name, target_db_name)
        create_database(connection, target_db_name)
        return False

    terminate_connections(connection, template_db_name)
    with connection.connect() as cur:
        cur.connection.autocommit = True
        LOGGER.info("Creating DB clone: %s from template: %s", target_db_name, template_db_name)
        strategy_sql = sql.SQL(" STRATEGY FILE_COPY") if use_file_copy_strategy else sql.SQL("")
        cur.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}{}").format(
                sql.Identifier(target_db_name),
                sql.Identifier(template_db_name),
                strategy_sql,
            )
        )
    return True


def template_source_name(db_name: str, db_template_name: str = "") -> str:
    """Return template source database name."""
    return db_template_name or f"{db_name}_template"
