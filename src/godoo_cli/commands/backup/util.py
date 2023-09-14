import subprocess
from logging import getLogger
from pathlib import Path

from ..db.connection import DBConnection

LOGGER = getLogger(__name__)


def call_rsync(source_folder: Path, target_folder: Path, rsync_delete: bool = True):
    """Copy Folder to anohter using Rsync"""
    if not source_folder.exists() and not source_folder.glob("*"):
        raise FileNotFoundError("Cannot find filestore Backup @ %s" % source_folder)
    args = ["rsync", "-a", "--no-perms", "--no-owner", "--no-group", "--info=progress2"]
    if rsync_delete:
        args += ["--delete"]
    args += [f"{source_folder}/", f"{target_folder}/"]
    command = " ".join(map(str, args))
    LOGGER.info("Rysnc filestore to: %s", source_folder)
    LOGGER.debug("Running: %s", command)
    return subprocess.run(command, shell=True).returncode


def drop_db(connection: DBConnection, db_name: str):
    """Drop a DB in postgres"""
    with connection.connect() as cur:
        cur.connection.autocommit = True
        LOGGER.info("Dropping DB: %s", db_name)
        cur.execute("DROP DATABASE IF EXISTS %s" % db_name)


def create_db(connection: DBConnection, db_name: str):
    """Create DB In Postgres"""
    with connection.connect() as cur:
        cur.connection.autocommit = True
        LOGGER.info("Creating DB: %s", db_name)
        cur.execute("CREATE DATABASE %s" % db_name)
