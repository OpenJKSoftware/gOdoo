"""Thin wrappers around Odoo's database archive commands."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

from ..database.connection import DBConnection
from ..database.postgres import postgres_argv, postgres_environment
from ..database.state import base_module_major, database_exists
from .locks import begin_runtime_restore, runtime_data_directory, runtime_locks
from .odoo import (
    odoo_bin_get_version,
    odoo_database_args,
    require_supported_odoo_major,
    run_odoo_command,
)
from .promotion import (
    DatabaseCleaner,
    DatabaseRollback,
    DatabaseSwapper,
    RuntimeRestoreError,
    cleanup_database,
    complete_runtime_promotion,
    create_database,
    replace_filestore,
    rollback_database_swap,
    runtime_filestore_path,
    swap_database,
    temporary_database_name,
)
from .reset import odoo_db_command
from .restore import restore_custom_runtime

LOGGER = logging.getLogger(__name__)

CommandRunner = Callable[[Sequence[str]], int]

LEGACY_DUMP_FILENAME = "odoo.dump"
LEGACY_FILESTORE_DIRECTORY = "odoo_filestore"


def _uses_native_db_commands(odoo_version: int | None, odoo_bin_path: Path | None = None) -> bool:
    """Return whether this Odoo checkout provides native database commands."""
    if odoo_version is None:
        if odoo_bin_path is None:
            message = "Cannot select database archive commands without an Odoo binary path."
            raise RuntimeRestoreError(message)
        try:
            odoo_version = odoo_bin_get_version(odoo_bin_path.parent).major
        except ValueError as error:
            message = "Could not determine the Odoo version for database archive commands."
            raise RuntimeRestoreError(message) from error
    require_supported_odoo_major(odoo_version, odoo_bin_path.parent if odoo_bin_path else Path("odoo"))
    return odoo_version >= 19


def _run_odoo_db(command: Sequence[str]) -> int:
    """Run an Odoo database archive command without invoking a shell."""
    LOGGER.info("Running Odoo database command")
    return run_odoo_command(command).returncode


def _run_native_archive_load(command: Sequence[str], connection: DBConnection) -> int:
    """Make Odoo's psql child stop at the first SQL error during native restore."""
    with tempfile.TemporaryDirectory(prefix="godoo-psql-") as directory:
        psqlrc = Path(directory) / "psqlrc"
        psqlrc.write_text("\\set ON_ERROR_STOP on\n")
        with postgres_environment(connection, on_error_stop=False) as environment:
            environment["PSQLRC"] = str(psqlrc)
            return run_odoo_command(command, env=environment).returncode


def _run_postgres_command(command: Sequence[str], connection: DBConnection) -> int:
    """Run a PostgreSQL fallback with credentials outside argv."""
    with postgres_environment(connection, on_error_stop=True) as environment:
        return run_odoo_command(command, env=environment).returncode


def dump_runtime_archive(
    *,
    db_name: str,
    archive_path: Path,
    odoo_bin_path: Path,
    odoo_conf_path: Path | None = None,
    data_dir: Path | None = None,
    runner: CommandRunner | None = None,
    odoo_version: int | None = None,
    connection: DBConnection | None = None,
) -> int:
    """Create an archive atomically, retaining any prior destination on failure."""
    with runtime_locks(data_dir, db_name, odoo_conf_path=odoo_conf_path):
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = archive_path.with_name(f".{archive_path.name}.{uuid.uuid4().hex}.tmp")
        sql_temporary = temporary.with_suffix(".sql")
        try:
            target_connection = connection or _archive_connection(db_name, odoo_conf_path)
            runtime_data = runtime_data_directory(data_dir, odoo_conf_path)
            native = _uses_native_db_commands(odoo_version, odoo_bin_path)
            if native:
                command = odoo_db_command(
                    odoo_bin_path=odoo_bin_path,
                    odoo_conf_path=odoo_conf_path,
                    data_dir=data_dir,
                    arguments=["dump", db_name, str(temporary)],
                )
            else:
                command = [
                    "pg_dump",
                    "--no-owner",
                    "--format=plain",
                    "--file",
                    str(sql_temporary),
                    *postgres_argv(target_connection),
                ]
            if runner is not None:
                result = runner(command)
            elif native:
                result = _run_odoo_db(command)
            else:
                result = _run_postgres_command(command, target_connection)
            if result:
                return result
            if not native:
                filestore = runtime_filestore_path(runtime_data, db_name)
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.write(sql_temporary, "dump.sql")
                    if filestore.is_dir():
                        for path in sorted(filestore.rglob("*")):
                            if path.is_file():
                                archive.write(path, Path("filestore") / path.relative_to(filestore))
            os.replace(temporary, archive_path)
            return 0
        finally:
            temporary.unlink(missing_ok=True)
            sql_temporary.unlink(missing_ok=True)


def validate_native_runtime_archive(archive_path: Path) -> None:
    """Validate Odoo's ZIP structure and CRCs before allowing a forced load."""
    if not archive_path.is_file():
        message = f"Odoo runtime archive does not exist or is not a file: {archive_path}"
        raise RuntimeRestoreError(message)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if "dump.sql" not in archive.namelist():
                message = f"Odoo runtime archive does not contain dump.sql: {archive_path}"
                raise RuntimeRestoreError(message)
            if bad_member := archive.testzip():
                message = f"Odoo runtime archive contains a corrupt member '{bad_member}': {archive_path}"
                raise RuntimeRestoreError(message)
    except zipfile.BadZipFile as error:
        message = f"Odoo runtime archive is not a valid ZIP file: {archive_path}"
        raise RuntimeRestoreError(message) from error


def _validate_staged_base_version(connection: DBConnection, target_major: int) -> None:
    """Ensure staged Odoo restore contains the configured base module major."""
    try:
        actual_major = base_module_major(connection)
    except RuntimeError as error:
        message = "Could not read a usable base module version from staged Odoo database."
        raise RuntimeRestoreError(message) from error
    if actual_major != target_major:
        message = f"The staged Odoo database version {actual_major}; runtime requires Odoo {target_major}."
        raise RuntimeRestoreError(message)


def load_runtime_archive(  # noqa: C901
    *,
    db_name: str,
    archive_path: Path,
    odoo_bin_path: Path,
    odoo_conf_path: Path | None = None,
    data_dir: Path | None = None,
    force: bool = False,
    runner: CommandRunner | None = None,
    connection: DBConnection | None = None,
    database_cleaner: DatabaseCleaner = cleanup_database,
    database_swapper: DatabaseSwapper = swap_database,
    database_rollback: DatabaseRollback = rollback_database_swap,
    odoo_version: int | None = None,
    database_creator: Callable[[DBConnection, str], None] = create_database,
    use_native_db_load: bool | None = None,
) -> int:
    """Stage a native restore and promote its database and filestore together."""
    # Validate replacement eligibility while the target runtime remains stable.
    if odoo_version is None:
        target_major = odoo_bin_get_version(odoo_bin_path.parent).major
        require_supported_odoo_major(target_major, odoo_bin_path.parent)
    else:
        target_major = require_supported_odoo_major(odoo_version, odoo_bin_path.parent)
    with runtime_locks(data_dir, db_name, odoo_conf_path=odoo_conf_path):
        validate_native_runtime_archive(archive_path)
        target_connection = connection or _archive_connection(db_name, odoo_conf_path)
        if target_connection.db_name != db_name:
            message = "Archive connection database must match the requested runtime."
            raise ValueError(message)
        runtime_data = runtime_data_directory(data_dir, odoo_conf_path)
        target = runtime_filestore_path(runtime_data, db_name)
        nonempty_filestore = target.is_dir() and next(target.iterdir(), None) is not None
        if not force and (
            database_exists(target_connection.with_db("postgres"), db_name)
            or nonempty_filestore
            or (target.exists() and not target.is_dir())
        ):
            message = f"Runtime '{db_name}' already exists; pass --force to replace its database and filestore."
            raise RuntimeRestoreError(message)
        # Build an isolated database and filestore before either live artifact is replaced.
        staged_database = temporary_database_name("native_restore")
        extracted: tempfile.TemporaryDirectory | None = None
        native = (
            _uses_native_db_commands(target_major, odoo_bin_path) if use_native_db_load is None else use_native_db_load
        )
        if native:
            command = odoo_db_command(
                odoo_bin_path=odoo_bin_path,
                odoo_conf_path=odoo_conf_path,
                data_dir=runtime_data,
                arguments=[
                    *odoo_database_args(
                        db_name=None,
                        db_user=target_connection.username,
                        db_password=target_connection.password,
                        db_host=target_connection.hostname,
                        db_port=target_connection.port,
                        db_sslmode=target_connection.sslmode,
                    ),
                    "load",
                    staged_database,
                    str(archive_path),
                ],
            )
        else:
            extracted = tempfile.TemporaryDirectory(prefix="godoo-archive-")
            with zipfile.ZipFile(archive_path) as source:
                source.extract("dump.sql", extracted.name)
                for member in source.namelist():
                    if member.startswith("filestore/") and not member.endswith("/"):
                        source.extract(member, extracted.name)
            command = [
                "psql",
                "--file",
                str(Path(extracted.name) / "dump.sql"),
                *postgres_argv(target_connection, database=staged_database),
            ]
        stage = runtime_filestore_path(runtime_data, staged_database)
        marker = None
        try:
            if not native:
                database_creator(target_connection.with_db(staged_database), "template0")
            if runner is not None:
                result = runner(command)
            elif native:
                result = _run_native_archive_load(command, target_connection)
            else:
                result = _run_postgres_command(command, target_connection)
            if result:
                return result
            _validate_staged_base_version(target_connection.with_db(staged_database), target_major)
            if extracted is None:
                stage.mkdir(parents=True, exist_ok=True)
            else:
                source_filestore = Path(extracted.name) / "filestore"
                if source_filestore.is_dir():
                    shutil.copytree(source_filestore, stage)
                else:
                    stage.mkdir(parents=True, exist_ok=True)
            marker = begin_runtime_restore(runtime_data, db_name, staged_database)
            backup = database_swapper(target_connection, staged_database)
            complete_runtime_promotion(
                connection=target_connection,
                marker=marker,
                backup_database=backup,
                staged_filestore=stage,
                target_filestore=target,
                database_cleaner=database_cleaner,
                database_rollback=database_rollback,
                filestore_replacer=replace_filestore,
            )
            return 0
        finally:
            if extracted is not None:
                extracted.cleanup()
            pending = marker is not None and marker.exists()
            if not pending:
                database_cleaner(target_connection.with_db(staged_database))
            else:
                LOGGER.warning(
                    "Retaining native restore database %s because promotion remains pending", staged_database
                )
            if stage.exists() and not pending:
                try:
                    shutil.rmtree(stage)
                except OSError:
                    LOGGER.warning("Could not remove native restore staging filestore %s", stage, exc_info=True)
            elif stage.exists():
                LOGGER.warning("Retaining native restore staging filestore %s because promotion remains pending", stage)


def _archive_connection(db_name: str, odoo_conf_path: Path | None) -> DBConnection:
    """Read native database connection settings from the same Odoo config file."""
    return DBConnection.from_odoo_config(db_name, odoo_conf_path)


def _legacy_filestore_source(source_folder: Path, db_name: str) -> Path:
    """Select the matching filestore from a gOdoo 0.17 dump directory."""
    filestore_root = source_folder / LEGACY_FILESTORE_DIRECTORY / "filestore"
    expected = filestore_root / db_name
    if expected.is_dir():
        return expected
    candidates = sorted(path for path in filestore_root.iterdir() if path.is_dir()) if filestore_root.is_dir() else []
    if len(candidates) == 1:
        return candidates[0]
    message = (
        f"Legacy dump '{source_folder}' does not contain one unambiguous filestore for database '{db_name}'. "
        "Restore with the original database name or keep only that filestore in the dump."
    )
    raise RuntimeRestoreError(message)


def load_legacy_runtime_dump(
    *,
    db_name: str,
    source_folder: Path,
    data_dir: Path,
    db_template: str,
    connection: DBConnection | None = None,
    db_host: str = "",
    db_port: int = 0,
    db_user: str = "",
    db_password: str = "",
    db_sslmode: str | None = None,
    force: bool = False,
) -> None:
    """Load the directory format emitted by gOdoo 0.17's ``backup dump`` command."""
    restore_custom_runtime(
        connection=connection or DBConnection(db_host, db_port, db_user, db_password, db_name, sslmode=db_sslmode),
        db_template=db_template,
        dump_path=source_folder / LEGACY_DUMP_FILENAME,
        filestore_source=_legacy_filestore_source(source_folder, db_name),
        data_dir=data_dir,
        force=force,
    )
