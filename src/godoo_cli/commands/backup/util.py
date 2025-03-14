"""Utility functions for backup operations.

This module provides common utility functions used across backup operations,
including file transfer and system command execution.
"""

import logging
import subprocess
from pathlib import Path

from ..db.connection import DBConnection

LOGGER = logging.getLogger(__name__)


def call_rsync(source_folder: Path, target_folder: Path, rsync_delete: bool = True):
    """Copy folder to another using Rsync.

    Args:
        source_folder: Source path to copy from.
        target_folder: Target path to copy to.
        rsync_delete: If True, delete files in target that don't exist in source.

    Raises:
        FileNotFoundError: If source folder doesn't exist or is empty.
    """
    if not source_folder.exists() and not source_folder.glob("*"):
        msg = f"Cannot find filestore Backup @ {source_folder}"
        LOGGER.error(msg)
        raise FileNotFoundError(msg)
    args = ["rsync", "-a", "--no-perms", "--no-owner", "--no-group", "--info=progress2"]
    if rsync_delete:
        args += ["--delete"]
    args += [f"{source_folder}/", f"{target_folder}/"]
    command = " ".join(map(str, args))
    LOGGER.debug("Running: %s", command)
    return subprocess.run(command, shell=True).returncode


def drop_db(connection: DBConnection, db_name: str):
    """Drop a DB in postgres."""
    with connection.connect() as cur:
        cur.connection.autocommit = True
        LOGGER.info("Dropping DB: %s", db_name)
        cur.execute(f"DROP DATABASE IF EXISTS {db_name}")


def create_db(connection: DBConnection, db_name: str):
    """Create DB In Postgres."""
    with connection.connect() as cur:
        cur.connection.autocommit = True
        LOGGER.info("Creating DB: %s", db_name)
        cur.execute(f"CREATE DATABASE {db_name}")
