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
from .baseline import baseline_database_name, save_runtime_baseline
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
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


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
    connection: DBConnection,
    staged_database: str,
    dump_path: Path,
    runner: CommandRunner | None = None,
    restore_jobs: int = 1,
) -> int:
    """Restore one custom dump into staging and return pg_restore's status."""
    command = [
        "pg_restore",
        "--no-owner",
        "--no-privileges",
        "--verbose",
        f"--jobs={restore_jobs}",
        *postgres_argv(connection, database=staged_database),
        str(dump_path),
    ]
    if runner is not None and runner is not _run:
        return runner(command)
    with postgres_environment(connection) as environment:
        return run_odoo_command(command, env=environment).returncode


# Keep staging, promotion, and failure cleanup together.
def restore_custom_runtime(  # noqa: C901
    *,
    connection: DBConnection,
    data_dir: Path,
    db_template: str,
    dump_path: Path,
    filestore_source: Path,
    force: bool = False,
    lifecycle_owner: str | None = None,
    target_major: int | None = None,
    expected_dump_identity: DumpIdentity | None = None,
    keep_reset_baseline: bool = False,
    restore_jobs: int = 1,
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
    if restore_jobs < 1:
        message = "PostgreSQL restore jobs must be at least 1."
        raise RuntimeRestoreError(message)
    validate_custom_dump(dump_path, runner)
    _require_dump_identity(dump_path, expected_dump_identity, "after validation")
    validate_filestore(filestore_source)

    # Keep the live runtime stable until both staged artifacts are ready for promotion.
    lock_names = (
        (connection.db_name, baseline_database_name(connection.db_name))
        if keep_reset_baseline
        else (connection.db_name,)
    )
    with runtime_locks(data_dir, *lock_names):
        target_filestore = runtime_filestore_path(data_dir, connection.db_name)
        exists = database_exists(connection.with_db("postgres", readonly=True), connection.db_name)
        nonempty_filestore = target_filestore.is_dir() and next(target_filestore.iterdir(), None) is not None
        if not force and (
            exists or nonempty_filestore or (target_filestore.exists() and not target_filestore.is_dir())
        ):
            msg = f"Runtime '{connection.db_name}' already exists; pass --force to replace its database and filestore."
            raise RuntimeRestoreError(msg)
        staged_filestore = target_filestore.with_name(f".{target_filestore.name}.restore-{uuid.uuid4().hex}")
        # Stage the database separately so a failed dump never replaces the active runtime.
        staged_database = temporary_database_name("restore")
        marker: Path | None = None
        staged_database_created = False
        try:
            with ProgressPhase("Copying restore filestore", logger=LOGGER):
                copy_filestore(filestore_source, staged_filestore)
            database_creator(connection.with_db(staged_database), db_template)
            staged_database_created = True
            with ProgressPhase("Restoring custom database", logger=LOGGER) as progress:
                _require_dump_identity(dump_path, expected_dump_identity, "before restore")
                result = _restore_database_dump(
                    connection,
                    staged_database,
                    dump_path,
                    runner,
                    restore_jobs,
                )
                progress.failed = result != 0
            if result != 0:
                _raise_restore_failure(connection.db_name, result)
            _require_dump_identity(dump_path, expected_dump_identity, "during restore")
            if target_major is not None:
                _validate_staged_base_version(connection.with_db(staged_database), target_major)
            if keep_reset_baseline:
                save_runtime_baseline(
                    connection=connection,
                    source_database=staged_database,
                    source_filestore=staged_filestore,
                    data_dir=data_dir,
                )
            marker = begin_runtime_restore(data_dir, connection.db_name, staged_database)
            backup_database = database_swapper(connection, staged_database)
            complete_runtime_promotion(
                connection=connection,
                marker=marker,
                backup_database=backup_database,
                staged_filestore=staged_filestore,
                target_filestore=target_filestore,
                database_cleaner=database_cleaner,
                database_rollback=database_rollback,
                lifecycle_owner=lifecycle_owner,
            )
        except BaseException:
            if marker is None or not marker.exists():
                if staged_database_created:
                    database_cleaner(connection.with_db(staged_database))
                if staged_filestore.exists():
                    try:
                        shutil.rmtree(staged_filestore)
                    except OSError:
                        LOGGER.warning("Could not remove restore staging directory %s", staged_filestore, exc_info=True)
            elif staged_filestore.exists():
                LOGGER.warning("Retaining restore staging directory %s; promotion remains pending", staged_filestore)
            raise
