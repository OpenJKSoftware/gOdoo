"""Restore PostgreSQL custom dumps and filestores into a runtime pair."""

import logging
import shutil
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from ..database.connection import DBConnection
from ..database.postgres import postgres_argv, postgres_environment
from ..database.state import database_exists
from .filestore import copy_filestore
from .locks import begin_runtime_restore, runtime_locks
from .odoo import run_odoo_command
from .promotion import (
    DatabaseCleaner,
    DatabaseCreator,
    DatabaseRollback,
    DatabaseSwapper,
    RuntimeRestoreError,
    cleanup_database,
    complete_runtime_promotion,
    create_database,
    rollback_database_swap,
    runtime_filestore_path,
    swap_database,
    temporary_database_name,
)

LOGGER = logging.getLogger(__name__)

CommandRunner = Callable[[Sequence[str]], int]


def _run(command: Sequence[str]) -> int:
    """Run an external restore command."""
    LOGGER.info("Running command: %s", " ".join(command))
    return run_odoo_command(command).returncode


def validate_custom_dump(dump_path: Path, runner: CommandRunner = _run) -> None:
    """Check that ``dump_path`` is readable by ``pg_restore`` before mutation."""
    if not dump_path.is_file():
        message = f"PostgreSQL custom dump does not exist: {dump_path}"
        raise RuntimeRestoreError(message)
    if runner(["pg_restore", "--format=custom", "--list", str(dump_path)]) != 0:
        message = f"PostgreSQL custom-format dump is invalid: {dump_path}"
        raise RuntimeRestoreError(message)


def validate_filestore(source: Path) -> None:
    """Check that the supplied filestore is available before mutation."""
    if not source.is_dir():
        message = f"Filestore directory does not exist: {source}"
        raise RuntimeRestoreError(message)


def _raise_restore_failure(db_name: str, result: int) -> None:
    """Raise a consistently worded pg_restore failure outside cleanup scope."""
    message = f"pg_restore failed for database '{db_name}' (exit code {result})"
    raise RuntimeRestoreError(message)


def _restore_database_dump(
    connection: DBConnection, staged_database: str, dump_path: Path, runner: CommandRunner | None = None
) -> int:
    """Restore one custom dump into staging and return pg_restore's status."""
    command = [
        "pg_restore",
        "--no-owner",
        "--no-privileges",
        *postgres_argv(connection, database=staged_database),
        str(dump_path),
    ]
    if runner is not None and runner is not _run:
        return runner(command)
    with postgres_environment(connection) as environment:
        return run_odoo_command(command, env=environment).returncode


def restore_custom_runtime(
    *,
    connection: DBConnection,
    db_template: str,
    dump_path: Path,
    filestore_source: Path,
    data_dir: Path,
    force: bool = False,
    runner: CommandRunner = _run,
    database_creator: DatabaseCreator = create_database,
    database_cleaner: DatabaseCleaner = cleanup_database,
    database_swapper: DatabaseSwapper = swap_database,
    database_rollback: DatabaseRollback = rollback_database_swap,
) -> None:
    """Restore custom PostgreSQL and filestore artifacts as one runtime pair.

    Both artifacts are staged and validated before the existing runtime pair is
    promoted. A pending marker preserves the recovery boundary if promotion
    cannot be rolled back safely.

    Raises:
        RuntimeRestoreError: If the input artifacts are invalid or replacing the
            existing runtime requires ``force``.
    """
    validate_custom_dump(dump_path, runner)
    validate_filestore(filestore_source)

    # Keep the live runtime stable until both staged artifacts are ready for promotion.
    with runtime_locks(data_dir, connection.db_name):
        target = runtime_filestore_path(data_dir, connection.db_name)
        exists = database_exists(connection.with_db("postgres", readonly=True), connection.db_name)
        nonempty_filestore = target.is_dir() and next(target.iterdir(), None) is not None
        if not force and (exists or nonempty_filestore or (target.exists() and not target.is_dir())):
            msg = f"Runtime '{connection.db_name}' already exists; pass --force to replace its database and filestore."
            raise RuntimeRestoreError(msg)
        stage = target.with_name(f".{target.name}.restore-{uuid.uuid4().hex}")
        # Stage the database separately so a failed dump never replaces the active runtime.
        staged_database = temporary_database_name("restore")
        marker: Path | None = None
        staged_database_created = False
        try:
            copy_filestore(filestore_source, stage)
            database_creator(connection.with_db(staged_database), db_template)
            staged_database_created = True
            result = _restore_database_dump(connection, staged_database, dump_path, runner)
            if result != 0:
                _raise_restore_failure(connection.db_name, result)
            marker = begin_runtime_restore(data_dir, connection.db_name, staged_database)
            backup = database_swapper(connection, staged_database)
            complete_runtime_promotion(
                connection=connection,
                marker=marker,
                backup_database=backup,
                staged_filestore=stage,
                target_filestore=target,
                database_cleaner=database_cleaner,
                database_rollback=database_rollback,
            )
        except BaseException:
            if staged_database_created and (marker is None or not marker.exists()):
                database_cleaner(connection.with_db(staged_database))
            if stage.exists() and (marker is None or not marker.exists()):
                try:
                    shutil.rmtree(stage)
                except OSError:
                    LOGGER.warning("Could not remove restore staging directory %s", stage, exc_info=True)
            if stage.exists():
                LOGGER.warning("Retaining restore staging directory %s; promotion remains pending", stage)
            raise
