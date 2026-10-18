"""Restore PostgreSQL custom dumps and filestores into a runtime pair."""

import logging
import shutil
import subprocess
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from ..database.connection import DBConnection
from ..database.postgres import postgres_argv, postgres_environment
from ..database.state import base_module_major, database_exists
from ..helpers.progress import ProgressPhase
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
DumpIdentity = tuple[int, int, int, int, int]


def _run(command: Sequence[str]) -> int:
    """Run an external restore command."""
    LOGGER.info("Running command: %s", " ".join(command))
    return run_odoo_command(command).returncode


def _validate_custom_dump_exists(dump_path: Path) -> None:
    """Check that ``dump_path`` is readable by ``pg_restore`` before mutation."""
    if not dump_path.is_file():
        message = f"PostgreSQL custom dump does not exist: {dump_path}"
        raise RuntimeRestoreError(message)


def custom_dump_identity(dump_path: Path) -> DumpIdentity:
    """Return the file identity used to guard a validated custom dump."""
    try:
        stat = dump_path.stat()
    except OSError as error:
        message = f"Could not stat PostgreSQL custom dump: {dump_path}"
        raise RuntimeRestoreError(message) from error
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _validate_staged_base_version(connection: DBConnection, target_major: int) -> None:
    """Ensure the staged custom dump matches the configured Odoo major."""
    try:
        restored_major = base_module_major(connection)
    except RuntimeError as error:
        message = "Could not read a usable base module version from staged Odoo database."
        raise RuntimeRestoreError(message) from error
    if restored_major != target_major:
        message = f"Staged Odoo database major {restored_major} does not match configured runtime major {target_major}."
        raise RuntimeRestoreError(message)


def _require_dump_identity(dump_path: Path, expected: DumpIdentity | None, phase: str) -> None:
    """Reject a custom dump replaced after its initial validation."""
    if expected is not None and custom_dump_identity(dump_path) != expected:
        message = f"PostgreSQL custom dump changed {phase}: {dump_path}"
        raise RuntimeRestoreError(message)


def validate_custom_dump(dump_path: Path, runner: CommandRunner = _run) -> None:
    """Check that ``dump_path`` is readable by ``pg_restore`` before mutation."""
    _validate_custom_dump_exists(dump_path)
    command = ["pg_restore", "--format=custom", "--list", str(dump_path)]
    with ProgressPhase("Validating custom database dump", logger=LOGGER) as progress:
        result = run_odoo_command(command, stdout=subprocess.DEVNULL).returncode if runner is _run else runner(command)
        progress.failed = result != 0
    if result != 0:
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
        "--verbose",
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
    lifecycle_owner: str | None = None,
    target_major: int | None = None,
    expected_dump_identity: DumpIdentity | None = None,
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
    _require_dump_identity(dump_path, expected_dump_identity, "after validation")
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
            with ProgressPhase("Copying restore filestore", logger=LOGGER):
                copy_filestore(filestore_source, stage)
            database_creator(connection.with_db(staged_database), db_template)
            staged_database_created = True
            with ProgressPhase("Restoring custom database", logger=LOGGER) as progress:
                _require_dump_identity(dump_path, expected_dump_identity, "before restore")
                result = _restore_database_dump(connection, staged_database, dump_path, runner)
                progress.failed = result != 0
            if result != 0:
                _raise_restore_failure(connection.db_name, result)
            _require_dump_identity(dump_path, expected_dump_identity, "during restore")
            if target_major is not None:
                _validate_staged_base_version(connection.with_db(staged_database), target_major)
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
                lifecycle_owner=lifecycle_owner,
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
