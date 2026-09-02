"""Safe restoration of a PostgreSQL custom dump and Odoo filestore."""

import logging
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Optional

from psycopg2 import sql

from ...models import DBConnection

LOGGER = logging.getLogger(__name__)

CommandRunner = Callable[[Sequence[str]], int]
DatabaseCreator = Callable[[DBConnection, str], None]
DatabaseCleaner = Callable[[DBConnection], None]
DatabaseSwapper = Callable[[DBConnection, str], Optional[str]]
DatabaseRollback = Callable[[DBConnection, Optional[str]], None]


class RuntimeRestoreError(RuntimeError):
    """Raised when a custom runtime restore cannot complete safely."""


def runtime_filestore_path(data_dir: Path, db_name: str) -> Path:
    """Return the one filestore owned by a runtime database."""
    # Database names are used as directory names below.  Reject anything other
    # than one non-empty path component so an absolute name or ``..`` segment
    # cannot redirect restore operations outside the configured data directory.
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


def _run(command: Sequence[str]) -> int:
    LOGGER.info("Running restore command: %s", " ".join(command))
    return subprocess.run(list(command), check=False).returncode


def validate_custom_dump(dump_path: Path, runner: CommandRunner = _run) -> None:
    """Check that ``dump_path`` is readable by pg_restore before any mutation."""
    if not dump_path.is_file():
        message = f"PostgreSQL dump does not exist or is not a file: {dump_path}"
        raise RuntimeRestoreError(message)
    if runner(["pg_restore", "--format=custom", "--list", str(dump_path)]) != 0:
        message = f"PostgreSQL dump is not a valid custom-format dump: {dump_path}"
        raise RuntimeRestoreError(message)


def validate_filestore(source: Path) -> None:
    """Check that the separately mounted filestore is available."""
    if not source.is_dir():
        message = f"Filestore directory does not exist: {source}"
        raise RuntimeRestoreError(message)


def _raise_restore_failure(db_name: str, result: int) -> None:
    """Raise a consistently worded pg_restore failure outside cleanup scope."""
    message = f"pg_restore failed for database '{db_name}' (exit code {result})"
    raise RuntimeRestoreError(message)


def _temporary_database_name(label: str) -> str:
    """Return a collision-resistant PostgreSQL identifier within its 63-byte limit."""
    return f"godoo_{label}_{uuid.uuid4().hex}"[:63]


def _create_database(connection: DBConnection, template: str) -> None:
    """Create a clean staging database after restore preflight passed."""
    admin = connection.with_db("postgres").get_connection()
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", [connection.db_name]
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(connection.db_name)))
            cursor.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                    sql.Identifier(connection.db_name), sql.Identifier(template)
                )
            )
    finally:
        admin.close()


def _drop_database(connection: DBConnection) -> None:
    """Best-effort cleanup of a staging or retained backup database."""
    try:
        admin = connection.with_db("postgres").get_connection()
        admin.autocommit = True
        try:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", [connection.db_name]
                )
                cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(connection.db_name)))
        finally:
            admin.close()
    except Exception:  # pragma: no cover - only exercised when cleanup itself is unavailable
        LOGGER.exception("Could not clean up restore database '%s'", connection.db_name)


def _swap_database(connection: DBConnection, staged_database: str) -> Optional[str]:
    """Promote a restored staging database while retaining the previous target."""
    admin = connection.with_db("postgres").get_connection()
    admin.autocommit = True
    backup_database: Optional[str] = None
    try:
        with admin.cursor() as cursor:
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)", [connection.db_name])
            target_exists = bool(cursor.fetchone()[0])
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN (%s, %s)",
                [connection.db_name, staged_database],
            )
            if target_exists:
                backup_database = _temporary_database_name("before_restore")
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


def _rollback_database_swap(connection: DBConnection, backup_database: Optional[str]) -> None:
    """Remove the promoted restore and reinstate the previous target database."""
    admin = connection.with_db("postgres").get_connection()
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", [connection.db_name]
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


def _restore_database_dump(
    connection: DBConnection,
    staged_database: str,
    dump_path: Path,
    runner: CommandRunner,
) -> int:
    """Restore one dump into its staging database and return pg_restore's status."""
    command = ["pg_restore", "--no-owner", "--no-privileges", "--dbname", staged_database]
    if connection.hostname:
        command.extend(["--host", connection.hostname])
    if connection.port:
        command.extend(["--port", str(connection.port)])
    if connection.username:
        command.extend(["--username", connection.username])
    command.append(str(dump_path))
    if runner is not _run:
        return runner(command)
    environment = os.environ.copy()
    if connection.password:
        environment["PGPASSWORD"] = connection.password
    return subprocess.run(command, check=False, env=environment).returncode


def _replace_filestore(stage: Path, target: Path) -> None:
    """Atomically swap a staged filestore, rolling back the old directory on error."""
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
    else:
        if moved_previous:
            try:
                shutil.rmtree(previous)
            except OSError:
                # The new filestore is already live at this point. Retain the
                # old one as a recoverable backup rather than converting a
                # successful restore into a database/filestore mismatch.
                LOGGER.warning("Could not remove previous filestore backup; retaining %s", previous, exc_info=True)


def restore_custom_runtime(
    *,
    connection: DBConnection,
    db_template: str,
    dump_path: Path,
    filestore_source: Path,
    data_dir: Path,
    runner: CommandRunner = _run,
    database_creator: DatabaseCreator = _create_database,
    database_cleaner: DatabaseCleaner = _drop_database,
    database_swapper: DatabaseSwapper = _swap_database,
    database_rollback: DatabaseRollback = _rollback_database_swap,
) -> None:
    """Restore local custom PostgreSQL and filestore artifacts as one runtime.

    Both artifacts are validated and the filestore copied to a sibling staging
    directory before restoring into a temporary database. Only a complete
    database restore is promoted, and a failed filestore swap reinstates the
    previous database so the runtime remains a consistent pair.
    """
    target = runtime_filestore_path(data_dir, connection.db_name)
    validate_custom_dump(dump_path, runner)
    validate_filestore(filestore_source)
    stage = target.with_name(f".{target.name}.restore-{uuid.uuid4().hex}")
    staged_database = _temporary_database_name("restore")
    staged_database_created = False
    database_swapped = False
    backup_database: Optional[str] = None
    try:
        shutil.copytree(filestore_source, stage)
        database_creator(connection.with_db(staged_database), db_template)
        staged_database_created = True
        result = _restore_database_dump(connection, staged_database, dump_path, runner)
        if result:
            _raise_restore_failure(connection.db_name, result)
        backup_database = database_swapper(connection, staged_database)
        staged_database_created = False
        database_swapped = True
        _replace_filestore(stage, target)
    except Exception:
        if database_swapped:
            database_rollback(connection, backup_database)
        raise
    else:
        if backup_database is not None:
            database_cleaner(connection.with_db(backup_database))
    finally:
        if staged_database_created:
            database_cleaner(connection.with_db(staged_database))
        if stage.exists():
            try:
                shutil.rmtree(stage)
            except OSError:
                LOGGER.warning("Could not remove restore staging directory %s", stage, exc_info=True)
