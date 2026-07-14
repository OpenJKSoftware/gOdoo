"""Database reset command implementation."""

import logging
from pathlib import Path
from typing import Annotated, Optional

from ...cli_common import CommonCLI
from ...models import DBConnection
from .clone import create_database_from_template, template_source_name
from .ops import database_exists, drop_database

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def _clear_odoo_config_file(odoo_conf_path: Path) -> bool:
    """Delete odoo.conf file if it exists."""
    try:
        if odoo_conf_path.exists():
            LOGGER.info("Removing Odoo config file: %s", odoo_conf_path)
            odoo_conf_path.unlink()
        else:
            LOGGER.info("Odoo config file already absent: %s", odoo_conf_path)
    except OSError:
        LOGGER.exception("Could not remove Odoo config file '%s'", odoo_conf_path)
        return False
    return True


def _reset_from_template_impl(
    db_name: str,
    db_user: str,
    db_template_name: str = "",
    db_host: str = "",
    db_port: int = 0,
    db_password: str = "",
    clear_config_on_missing_template: bool = False,
    odoo_conf_path: Optional[Path] = None,
) -> int:
    """Core reset flow used by both root and db commands."""
    template_db_name = template_source_name(db_name, db_template_name)

    connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name="postgres",
    )

    if template_db_name == db_name:
        LOGGER.error("Template DB '%s' must differ from target DB '%s'.", template_db_name, db_name)
        return CLI.returner(1)

    if not database_exists(connection=connection, db_name=template_db_name):
        LOGGER.warning("Template DB '%s' not found. Dropping target DB '%s' only.", template_db_name, db_name)
        drop_database(connection=connection, db_name=db_name)
        if (
            clear_config_on_missing_template
            and odoo_conf_path is not None
            and not _clear_odoo_config_file(odoo_conf_path=odoo_conf_path)
        ):
            return CLI.returner(1)
        return CLI.returner(0)

    try:
        create_database_from_template(
            connection=connection,
            template_db_name=template_db_name,
            target_db_name=db_name,
            recreate_target=True,
            allow_fallback_create=False,
            use_file_copy_strategy=True,
        )
    except ValueError:
        LOGGER.exception("Reset DB failed for '%s' (template='%s')", db_name, template_db_name)
        return CLI.returner(1)

    LOGGER.info("Reset DB complete: %s (template=%s)", db_name, template_db_name)
    return CLI.returner(0)


def reset_database_from_template(
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_template_name: Annotated[str, CLI.database.db_template_name] = "",
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
) -> int:
    """Reset a database from template.

    Behavior:
        - If ``db_template_name`` is provided, clone from that template.
        - If ``db_template_name`` is omitted, clone from ``<db_name>_template``.
        - If the template does not exist, only drop the target DB and exit success.
        - If template and target names are equal, exit with an error to avoid
          destructive self-targeting.
    """
    return _reset_from_template_impl(
        db_name=db_name,
        db_user=db_user,
        db_template_name=db_template_name,
        db_host=db_host,
        db_port=db_port,
        db_password=db_password,
        clear_config_on_missing_template=False,
    )


def reset_odoo_state(
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_template_name: Annotated[str, CLI.database.db_template_name] = "",
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
) -> int:
    """Reset Odoo runtime state.

    Behavior:
        - Delegates database work to ``reset_database_from_template``.
        - If template DB is missing, additionally deletes odoo.conf.
    """
    template_db_name = template_source_name(db_name, db_template_name)
    template_missing = False
    if template_db_name != db_name:
        template_missing = not database_exists(
            connection=DBConnection(
                hostname=db_host,
                port=db_port,
                username=db_user,
                password=db_password,
                db_name="postgres",
            ),
            db_name=template_db_name,
        )

    reset_result = reset_database_from_template(
        db_name=db_name,
        db_user=db_user,
        db_template_name=db_template_name,
        db_host=db_host,
        db_port=db_port,
        db_password=db_password,
    )
    if reset_result != 0:
        return CLI.returner(reset_result)

    if template_missing and not _clear_odoo_config_file(odoo_conf_path=odoo_conf_path):
        return CLI.returner(1)

    return CLI.returner(0)
