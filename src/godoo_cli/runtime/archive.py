"""Thin wrappers around Odoo's database archive commands."""

from __future__ import annotations

import logging
import os
import posixpath
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..database.connection import DBConnection
from ..database.postgres import postgres_argv, postgres_environment
from ..database.state import base_module_major, database_exists
from ..helpers.progress import ProgressPhase as _ProgressPhase
from .filestore import copy_filestore
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
PROGRESS_INTERVAL_SECONDS = 5


@dataclass(frozen=True)
class _ArchiveFileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _ValidatedNativeArchive:
    identity: _ArchiveFileIdentity


def _archive_identity(stat_result: os.stat_result) -> _ArchiveFileIdentity:
    return _ArchiveFileIdentity(
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size=stat_result.st_size,
        modified_ns=stat_result.st_mtime_ns,
        changed_ns=stat_result.st_ctime_ns,
    )


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


def _run_postgres_command(command: Sequence[str], connection: DBConnection) -> int:  # noqa: C901
    """Run a PostgreSQL fallback with credentials outside argv."""
    with postgres_environment(connection, on_error_stop=True) as environment:
        try:
            file_index = command.index("--file")
            dump_path = Path(command[file_index + 1])
        except (ValueError, IndexError):
            return run_odoo_command(command, env=environment).returncode

        psql_command = list(command)
        psql_command[file_index + 1] = "-"
        if not shutil.which("pv"):
            return _stream_sql_input(psql_command, dump_path, environment)
        LOGGER.info("Sending SQL input from %s", dump_path)
        producer = subprocess.Popen(
            ["pv", "--force", "--interval", str(PROGRESS_INTERVAL_SECONDS), str(dump_path)],
            stdout=subprocess.PIPE,
        )

        try:
            assert producer.stdout is not None
            sql_result = run_odoo_command(
                psql_command, env=environment, stdin=producer.stdout, stdout=subprocess.DEVNULL
            ).returncode
        except BaseException:
            if producer.stdout is not None:
                producer.stdout.close()
            if producer.poll() is None:
                producer.terminate()
            try:
                producer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                producer.kill()
                producer.wait()
            raise
        if producer.stdout is not None:
            producer.stdout.close()
        if sql_result and producer.poll() is None:
            producer.terminate()
        try:
            producer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            producer.terminate()
            try:
                producer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                producer.kill()
                producer.wait()
        if sql_result:
            return sql_result
        if producer.returncode:
            LOGGER.error("SQL input failed with status %s", producer.returncode)
            return producer.returncode
        LOGGER.info("SQL input sent; PostgreSQL restore completed")
        return 0


def _stream_sql_input(command: Sequence[str], dump_path: Path, environment: dict[str, str]) -> int:
    """Send SQL through psql while reporting measured input bytes without pv."""
    total_bytes = dump_path.stat().st_size
    sent_bytes = 0
    last_report = time.monotonic()
    LOGGER.info("Sending SQL input: 0/%s bytes sent (0.0%%)", total_bytes)
    process: subprocess.Popen[bytes] | None = None

    def forward(signum: int, _frame: object) -> None:
        if process is not None and process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signum)

    previous: dict[int, signal.Handlers] = {}
    if threading.current_thread() is threading.main_thread():
        previous = {
            signum: cast(signal.Handlers, signal.signal(signum, forward)) for signum in (signal.SIGTERM, signal.SIGINT)
        }
    try:
        process = subprocess.Popen(
            command,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            start_new_session=True,
        )
        stdin = process.stdin
        assert stdin is not None
        try:
            with suppress(BrokenPipeError), dump_path.open("rb") as dump_file:
                while chunk := dump_file.read(1024 * 1024):
                    stdin.write(chunk)
                    sent_bytes += len(chunk)
                    if time.monotonic() - last_report >= PROGRESS_INTERVAL_SECONDS:
                        LOGGER.info(
                            "Sending SQL input: %s/%s bytes sent (%.1f%%)",
                            sent_bytes,
                            total_bytes,
                            (sent_bytes / total_bytes * 100) if total_bytes else 100,
                        )
                        last_report = time.monotonic()
            with suppress(BrokenPipeError):
                stdin.close()
            LOGGER.info(
                "Sending SQL input: %s/%s bytes sent (%.1f%%)",
                sent_bytes,
                total_bytes,
                (sent_bytes / total_bytes * 100) if total_bytes else 100,
            )
            result = process.wait()
        except BaseException:
            forward(signal.SIGKILL, None)
            with suppress(BrokenPipeError):
                stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forward(signal.SIGKILL, None)
                process.wait()
            raise
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if result:
        return result if result >= 0 else 128 - result
    LOGGER.info("SQL input sent; PostgreSQL restore completed")
    return 0


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
            with _ProgressPhase("Dumping runtime database", logger=LOGGER) as progress:
                if runner is not None:
                    result = runner(command)
                elif native:
                    result = _run_odoo_db(command)
                else:
                    result = _run_postgres_command(command, target_connection)
                progress.failed = result != 0
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


def _validate_native_runtime_archive_contents(archive_path: Path) -> _ValidatedNativeArchive:  # noqa: C901
    """Validate an Odoo ZIP and retain the identity that was checked."""
    if not archive_path.is_file():
        message = f"Odoo runtime archive does not exist or is not a file: {archive_path}"
        raise RuntimeRestoreError(message)
    try:
        with archive_path.open("rb") as archive_file:
            identity = _archive_identity(os.fstat(archive_file.fileno()))
            with zipfile.ZipFile(archive_file) as archive:
                if "dump.sql" not in archive.namelist():
                    message = f"Odoo runtime archive does not contain dump.sql: {archive_path}"
                    raise RuntimeRestoreError(message)
                members = [info for info in archive.infolist() if not info.is_dir()]
                total_bytes = sum(info.file_size for info in members)
                read_bytes = 0
                next_report = time.monotonic() + PROGRESS_INTERVAL_SECONDS
                for info in members:
                    try:
                        with archive.open(info) as member:
                            while chunk := member.read(1024 * 1024):
                                read_bytes += len(chunk)
                                if time.monotonic() >= next_report and total_bytes:
                                    LOGGER.info(
                                        "Validating runtime archive CRCs: %d%% (%d/%d bytes)",
                                        read_bytes * 100 // total_bytes,
                                        read_bytes,
                                        total_bytes,
                                    )
                                    next_report = time.monotonic() + PROGRESS_INTERVAL_SECONDS
                    except zipfile.BadZipFile as error:
                        message = f"Odoo runtime archive contains corrupt member '{info.filename}': {archive_path}"
                        raise RuntimeRestoreError(message) from error
                if total_bytes:
                    LOGGER.info("Validated runtime archive CRCs: 100%% (%d/%d bytes)", read_bytes, total_bytes)
                for info in archive.infolist():
                    if not info.filename.startswith("filestore/"):
                        continue
                    member = info.filename
                    if "\\" in member or member.startswith("/") or posixpath.normpath(member) != member.rstrip("/"):
                        message = f"Odoo runtime archive contains unsafe filestore path: {member}"
                        raise RuntimeRestoreError(message)
                if identity != _archive_identity(os.fstat(archive_file.fileno())):
                    message = f"Odoo runtime archive changed while it was being validated: {archive_path}"
                    raise RuntimeRestoreError(message)
    except zipfile.BadZipFile as error:
        message = f"Odoo runtime archive is not a valid ZIP file: {archive_path}"
        raise RuntimeRestoreError(message) from error
    except OSError as error:
        message = f"Could not read Odoo runtime archive: {archive_path}"
        raise RuntimeRestoreError(message) from error
    return _ValidatedNativeArchive(identity)


def validate_native_runtime_archive(archive_path: Path) -> None:
    """Validate Odoo's ZIP structure and CRCs before allowing a forced load."""
    _validate_native_runtime_archive(archive_path)


def _validate_native_runtime_archive(archive_path: Path) -> _ValidatedNativeArchive:
    """Validate a native archive while reporting progress for CRC checks."""
    with _ProgressPhase("Validating runtime archive", heartbeat=False, logger=LOGGER):
        return _validate_native_runtime_archive_contents(archive_path)


def _reuse_or_validate_archive(
    archive_path: Path,
    validated_archive: _ValidatedNativeArchive | None,
    *,
    require_same_identity: bool = False,
) -> _ValidatedNativeArchive:
    if validated_archive is None and require_same_identity:
        message = "Lifecycle archive identity was not captured before initialization."
        raise RuntimeRestoreError(message)
    if validated_archive is not None:
        try:
            current_identity = _archive_identity(archive_path.stat())
        except OSError:
            current_identity = None
        if current_identity == validated_archive.identity:
            return validated_archive
        if require_same_identity:
            message = "Odoo runtime archive changed after lifecycle plan validation."
            raise RuntimeRestoreError(message)
    return _validate_native_runtime_archive(archive_path)


@contextmanager
def _open_validated_archive(
    archive_path: Path,
    validated_archive: _ValidatedNativeArchive,
) -> Iterator[zipfile.ZipFile]:
    """Open the same archive file whose contents passed CRC validation."""
    try:
        archive_file = archive_path.open("rb")
    except OSError as error:
        message = f"Could not read Odoo runtime archive: {archive_path}"
        raise RuntimeRestoreError(message) from error
    with archive_file:
        if _archive_identity(os.fstat(archive_file.fileno())) != validated_archive.identity:
            message = f"Odoo runtime archive changed after validation: {archive_path}"
            raise RuntimeRestoreError(message)
        try:
            archive = zipfile.ZipFile(archive_file)
        except zipfile.BadZipFile as error:
            message = f"Odoo runtime archive is not a valid ZIP file: {archive_path}"
            raise RuntimeRestoreError(message) from error
        with archive:
            yield archive


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


def _overlay_filestore(archive: zipfile.ZipFile, destination: Path) -> None:
    """Write archive filestore members over a staged copy of the original."""
    for info in archive.infolist():
        if not info.filename.startswith("filestore/") or info.is_dir():
            continue
        relative = info.filename.removeprefix("filestore/")
        target = destination.joinpath(*relative.split("/"))
        parents: list[Path] = []
        parent = target.parent
        while parent != destination:
            parents.append(parent)
            parent = parent.parent
        for parent in reversed(parents):
            if parent.exists() and not parent.is_dir():
                parent.unlink()
            parent.mkdir(exist_ok=True)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
        with archive.open(info) as member, target.open("wb") as output:
            shutil.copyfileobj(member, output)


def load_runtime_archive(  # noqa: C901
    *,
    db_name: str,
    archive_path: Path,
    original_filestore: Path | None = None,
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
    _validated_archive: _ValidatedNativeArchive | None = None,
    lifecycle_owner: str | None = None,
    require_same_archive_identity: bool = False,
) -> int:
    """Stage a native restore and promote its database and filestore together."""
    # Validate replacement eligibility while the target runtime remains stable.
    if odoo_version is None:
        target_major = odoo_bin_get_version(odoo_bin_path.parent).major
        require_supported_odoo_major(target_major, odoo_bin_path.parent)
    else:
        target_major = require_supported_odoo_major(odoo_version, odoo_bin_path.parent)
    with runtime_locks(data_dir, db_name, odoo_conf_path=odoo_conf_path):
        validated_archive = _reuse_or_validate_archive(
            archive_path,
            _validated_archive,
            require_same_identity=require_same_archive_identity,
        )
        if original_filestore is not None and not original_filestore.is_dir():
            message = f"Filestore directory does not exist: {original_filestore}"
            raise RuntimeRestoreError(message)
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
        if original_filestore is not None:
            # Odoo's native load opens the restored registry before the staged
            # filestore can be overlaid, so restore SQL into an isolated DB.
            native = False
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
            with (
                _ProgressPhase("Extracting runtime database dump", logger=LOGGER),
                _open_validated_archive(archive_path, validated_archive) as source,
            ):
                source.extract("dump.sql", extracted.name)
                if original_filestore is None:
                    for member in source.namelist():
                        if member.startswith("filestore/") and not member.endswith("/"):
                            source.extract(member, extracted.name)
            command = [
                "psql",
                "--quiet",
                "--file",
                str(Path(extracted.name) / "dump.sql"),
                *postgres_argv(target_connection, database=staged_database),
            ]
        stage = runtime_filestore_path(runtime_data, staged_database)
        marker = None
        try:
            if original_filestore is not None:
                with _ProgressPhase("Copying original filestore", heartbeat=False, logger=LOGGER):
                    copy_filestore(original_filestore, stage)
                with (
                    _ProgressPhase("Overlaying archive filestore", logger=LOGGER),
                    _open_validated_archive(archive_path, validated_archive) as source,
                ):
                    _overlay_filestore(source, stage)
            if not native:
                database_creator(target_connection.with_db(staged_database), "template0")
            result = 0
            with _ProgressPhase("Restoring runtime database", heartbeat=False, logger=LOGGER) as progress:
                if runner is not None:
                    result = runner(command)
                elif native:
                    result = _run_native_archive_load(command, target_connection)
                else:
                    result = _run_postgres_command(command, target_connection)
                progress.failed = result != 0
            if result:
                return result
            with _ProgressPhase("Validating restored runtime", logger=LOGGER):
                _validate_staged_base_version(target_connection.with_db(staged_database), target_major)
            if extracted is None:
                stage.mkdir(parents=True, exist_ok=True)
            elif original_filestore is None:
                source_filestore = Path(extracted.name) / "filestore"
                if source_filestore.is_dir():
                    shutil.copytree(source_filestore, stage)
                else:
                    stage.mkdir(parents=True, exist_ok=True)
            marker = begin_runtime_restore(runtime_data, db_name, staged_database)
            backup = database_swapper(target_connection, staged_database)
            with _ProgressPhase("Promoting restored runtime", logger=LOGGER):
                complete_runtime_promotion(
                    connection=target_connection,
                    marker=marker,
                    backup_database=backup,
                    staged_filestore=stage,
                    target_filestore=target,
                    database_cleaner=database_cleaner,
                    database_rollback=database_rollback,
                    filestore_replacer=replace_filestore,
                    lifecycle_owner=lifecycle_owner,
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
