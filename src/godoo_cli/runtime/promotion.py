"""Promotion primitives for an Odoo database and its matching filestore."""

import logging
import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from psycopg2 import sql

from ..database.connection import DBConnection
from .locks import retire_runtime_lifecycle

LOGGER = logging.getLogger(__name__)

DatabaseCreator = Callable[[DBConnection, str], None]
DatabaseCleaner = Callable[[DBConnection], None]
DatabaseSwapper = Callable[[DBConnection, str], str | None]
DatabaseRollback = Callable[[DBConnection, str | None], None]
FilestoreReplacer = Callable[[Path, Path], Path | None]


class RuntimeRestoreError(RuntimeError):
    """Raised when a database-and-filestore operation cannot safely proceed."""


def runtime_filestore_path(data_dir: Path, db_name: str) -> Path:
    """Return the filestore path owned by one runtime database."""
    name = Path(db_name)
    if not db_name or db_name in {".", ".."} or name.name != db_name:
        message = f"Unsafe database name for filestore: {db_name!r}"
        raise RuntimeRestoreError(message)

    data_root = data_dir.resolve()
    filestore_root = (data_dir / "filestore").resolve()
    try:
        filestore_root.relative_to(data_root)
    except ValueError as error:
        message = f"Filestore directory escapes data directory: {filestore_root}"
        raise RuntimeRestoreError(message) from error
    return filestore_root / db_name


def temporary_database_name(label: str) -> str:
    """Return a collision-resistant PostgreSQL identifier within its 63-byte limit."""
    return f"godoo_{label}_{uuid.uuid4().hex}"[:63]


def create_database(connection: DBConnection, template: str) -> None:
    """Create a clean staging database from ``template``."""
    admin = connection.with_db("postgres").get_connection()
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                [connection.db_name],
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(connection.db_name)))
            cursor.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                    sql.Identifier(connection.db_name), sql.Identifier(template)
                )
            )
    finally:
        admin.close()


def drop_database_strict(connection: DBConnection) -> None:
    """Drop a database, propagating errors from the primary operation."""
    admin = connection.with_db("postgres").get_connection()
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                [connection.db_name],
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(connection.db_name)))
    finally:
        admin.close()


def cleanup_database(connection: DBConnection) -> None:
    """Best-effort cleanup of a staging or retained backup database."""
    try:
        drop_database_strict(connection)
    except Exception:  # pragma: no cover - only exercised when cleanup is unavailable
        LOGGER.exception("Could not clean up database '%s'", connection.db_name)


def swap_database(connection: DBConnection, staged_database: str) -> str | None:
    """Promote staging data while retaining the previous target as a backup."""
    admin = connection.with_db("postgres").get_connection()
    admin.autocommit = True
    backup_database: str | None = None
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)",
                [connection.db_name],
            )
            row = cursor.fetchone()
            if row is None:
                message = "PostgreSQL returned no result while checking restore target."
                raise RuntimeError(message)
            target_exists = bool(row[0])
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN (%s, %s)",
                [connection.db_name, staged_database],
            )
            if target_exists:
                backup_database = temporary_database_name("backup")
                cursor.execute(
                    sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                        sql.Identifier(connection.db_name), sql.Identifier(backup_database)
                    )
                )
            try:
                cursor.execute(
                    sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                        sql.Identifier(staged_database), sql.Identifier(connection.db_name)
                    )
                )
            except Exception:
                if backup_database is not None:
                    cursor.execute(
                        sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                            sql.Identifier(backup_database), sql.Identifier(connection.db_name)
                        )
                    )
                raise
    finally:
        admin.close()
    return backup_database


def rollback_database_swap(connection: DBConnection, backup_database: str | None) -> None:
    """Remove the promoted database and reinstate the previous target."""
    admin = connection.with_db("postgres").get_connection()
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                [connection.db_name],
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(connection.db_name)))
            if backup_database is not None:
                cursor.execute(
                    sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                        sql.Identifier(backup_database), sql.Identifier(connection.db_name)
                    )
                )
    finally:
        admin.close()


def replace_filestore(stage: Path, target: Path) -> Path | None:
    """Atomically replace ``target`` with staged data, restoring it on failure."""
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = target.with_name(f".{target.name}.before-restore-{uuid.uuid4().hex}")
    moved_previous = False
    try:
        if target.exists():
            os.replace(target, previous)
            moved_previous = True
        os.replace(stage, target)
    except OSError as error:
        if moved_previous and not target.exists() and previous.exists():
            os.replace(previous, target)
        message = f"Could not atomically replace filestore '{target}': {error}"
        raise RuntimeRestoreError(message) from error
    return previous if moved_previous else None


def complete_runtime_promotion(
    *,
    connection: DBConnection,
    marker: Path,
    backup_database: str | None,
    staged_filestore: Path,
    target_filestore: Path,
    database_cleaner: DatabaseCleaner,
    database_rollback: DatabaseRollback,
    filestore_replacer: FilestoreReplacer | None = None,
    lifecycle_owner: str | None = None,
) -> None:
    """Complete promotion, rolling the database back if filestore replacement fails."""
    try:
        previous_filestore = (filestore_replacer or replace_filestore)(staged_filestore, target_filestore)
    except BaseException:
        try:
            database_rollback(connection, backup_database)
        except BaseException as rollback_error:
            LOGGER.exception("Could not roll back database promotion '%s'", connection.db_name)
            message = (
                f"Could not roll back runtime promotion '{connection.db_name}'; "
                "pending marker was retained for recovery; the pending marker retained "
                "state needs inspection."
            )
            raise RuntimeRestoreError(message) from rollback_error
        LOGGER.exception(
            "Database rollback completed after filestore promotion failed; retaining %s until the pair is inspected",
            marker,
        )
        raise
    if previous_filestore is not None:
        try:
            shutil.rmtree(previous_filestore)
        except OSError:
            LOGGER.warning(
                "Could not remove previous filestore backup; retaining %s", previous_filestore, exc_info=True
            )
    if backup_database is not None:
        database_cleaner(connection.with_db(backup_database))
    # The marker is the recovery authority: clear it only after both old-pair
    # cleanup steps have completed successfully.
    retire_runtime_lifecycle(marker.parent.parent.parent, connection.db_name, owner=lifecycle_owner)
    marker.unlink()
