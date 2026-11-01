"""Coordinate online attachment retention and backup publication."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path

import psycopg2

from ..database.connection import DBConnection

LOGGER = logging.getLogger(__name__)


class BackupFormat(StrEnum):
    """Select a combined Odoo ZIP or a separate custom dump and filestore."""

    ZIP = "zip"
    DUMP = "dump"


@contextmanager
def retain_attachment_files(connection: DBConnection) -> Iterator[None]:
    """Defer the standard autovacuum job without locking attachment writes.

    Raises:
        RuntimeError: If the standard cleanup job is missing or already running.
    """
    with connection.connect() as cursor:
        try:
            cursor.execute(
                "SELECT cron.id FROM ir_cron AS cron "
                "JOIN ir_model_data AS data ON data.res_id = cron.id AND data.model = 'ir.cron' "
                "WHERE data.module = 'base' AND data.name = 'autovacuum_job' "
                "FOR NO KEY UPDATE OF cron NOWAIT"
            )
        except psycopg2.errors.LockNotAvailable as error:
            message = "Odoo attachment cleanup is running; retry the backup after it finishes."
            raise RuntimeError(message) from error
        if len(cursor.fetchall()) != 1:
            message = "Cannot retain attachment files: Odoo's base.autovacuum_job is missing."
            raise RuntimeError(message)
        LOGGER.info("Deferring Odoo's internal autovacuum job during backup")
        yield
        # A server timeout or lost connection must fail before publication.
        cursor.execute("SELECT 1")
    LOGGER.info("Released Odoo's internal autovacuum job after backup")


def publish_backup_directory(staged: Path, destination: Path) -> None:
    """Promote a complete pair, restoring the previous pair on rename failure.

    Raises:
        RuntimeError: If a previous promotion needs recovery.
        OSError: If promotion fails, after attempting to restore the previous pair.
    """
    previous = destination.with_name(f".{destination.name}.previous")
    if previous.exists():
        message = f"Backup recovery is required: inspect {previous} and {destination} before retrying."
        raise RuntimeError(message)
    had_previous = destination.exists()
    if had_previous:
        os.replace(destination, previous)
    try:
        os.replace(staged, destination)
    except OSError:
        if had_previous:
            LOGGER.warning("Restoring previous backup from %s", previous)
            os.replace(previous, destination)
        raise
    if had_previous:
        try:
            shutil.rmtree(previous)
        except OSError:
            LOGGER.warning("Backup completed, but previous backup remains at %s", previous, exc_info=True)
