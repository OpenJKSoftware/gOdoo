"""Retain restored database and filestore pairs for repeatable resets."""

import hashlib
import logging
import shutil
from pathlib import Path

from ..database.connection import DBConnection
from ..helpers.progress import ProgressPhase
from .filestore import copy_filestore
from .locks import begin_runtime_restore, runtime_locks
from .promotion import (
    cleanup_database,
    complete_runtime_promotion,
    create_database,
    rollback_database_swap,
    runtime_filestore_path,
    swap_database,
    temporary_database_name,
)

LOGGER = logging.getLogger(__name__)


def baseline_database_name(db_name: str) -> str:
    """Return a stable, PostgreSQL-safe name reserved for this runtime's baseline."""
    return f"godoo_baseline_{hashlib.sha256(db_name.encode()).hexdigest()[:32]}"


def save_runtime_baseline(
    *,
    connection: DBConnection,
    source_database: str,
    source_filestore: Path,
    data_dir: Path,
) -> None:
    """Save the restored pair before initialization changes it.

    The caller holds the runtime and baseline locks throughout restoration.
    Replace an older baseline only after both new copies are ready.
    """
    baseline_name = baseline_database_name(connection.db_name)
    baseline_connection = connection.with_db(baseline_name)
    with runtime_locks(data_dir, connection.db_name, baseline_name):
        copied_database = temporary_database_name("baseline")
        copied_filestore = runtime_filestore_path(data_dir, copied_database)
        marker: Path | None = None
        try:
            with ProgressPhase("Saving reset baseline", logger=LOGGER):
                copy_filestore(source_filestore, copied_filestore)
                create_database(baseline_connection.with_db(copied_database), source_database, prefer_cow=True)
                marker = begin_runtime_restore(data_dir, baseline_name, copied_database, replace_pending=True)
                previous_database = swap_database(baseline_connection, copied_database)
                complete_runtime_promotion(
                    connection=baseline_connection,
                    marker=marker,
                    backup_database=previous_database,
                    staged_filestore=copied_filestore,
                    target_filestore=runtime_filestore_path(data_dir, baseline_name),
                    database_cleaner=cleanup_database,
                    database_rollback=rollback_database_swap,
                )
            LOGGER.info("Saved reset baseline %s for %s", baseline_name, connection.db_name)
        finally:
            if marker is None or not marker.exists():
                cleanup_database(baseline_connection.with_db(copied_database))
                if copied_filestore.exists():
                    shutil.rmtree(copied_filestore)
