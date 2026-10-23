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
from contextlib import ExitStack, contextmanager, nullcontext, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..database.connection import DBConnection
from ..database.postgres import postgres_argv, postgres_environment
from ..database.state import base_module_major, database_exists
from ..helpers.progress import ProgressPhase as _ProgressPhase
from .baseline import baseline_database_name, save_runtime_baseline
from .filestore import copy_filestore
from .locks import begin_runtime_restore, runtime_data_directory, runtime_locks
from .odoo import (
    odoo_bin_get_version,
    require_supported_odoo_major,
    run_odoo_command,
)
from .promotion import (
    DatabaseCleaner,
    DatabaseCreator,
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


def _archive_identity(stat_result: os.stat_result) -> _ArchiveFileIdentity:
    return _ArchiveFileIdentity(
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size=stat_result.st_size,
        modified_ns=stat_result.st_mtime_ns,
        changed_ns=stat_result.st_ctime_ns,
    )


@dataclass(frozen=True)
class _ValidatedNativeArchive:
    """Validated archive identity with an optional temporary extraction."""

    identity: _ArchiveFileIdentity
    extracted: tempfile.TemporaryDirectory[str] | None = None

    def cleanup(self) -> None:
        """Discard the temporary extraction; repeated calls are safe."""
        if self.extracted is not None:
            self.extracted.cleanup()


def prepare_archive_with_original_filestore(
    archive_path: Path | None,
    original_filestore: Path | None,
    *,
    verify_archive_crc: bool = False,
) -> _ValidatedNativeArchive | None:
    """Validate the original filestore and extract the validated ZIP.

    Returns:
        The extracted native archive, or ``None`` when no original filestore
        was supplied. Callers clean up the returned archive token.

    Raises:
        ValueError: If the filestore or archive path is invalid.
    """
    if original_filestore is None:
        return None
    if not original_filestore.is_dir():
        message = f"Original filestore directory does not exist: {original_filestore}"
        raise ValueError(message)
    if archive_path is None:
        message = "An Odoo ZIP archive is required with --original-filestore."
        raise ValueError(message)
    if archive_path.is_dir():
        message = "--original-filestore cannot be used with a legacy archive directory."
        raise ValueError(message)
    if archive_path.suffix.lower() != ".zip":
        message = "--original-filestore requires an Odoo ZIP archive."
        raise ValueError(message)
    if not archive_path.is_file():
        message = f"Odoo ZIP archive does not exist: {archive_path}"
        raise ValueError(message)
    return _extract_native_runtime_archive(archive_path, verify_archive_crc=verify_archive_crc)


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


def _validate_native_runtime_archive_contents(  # noqa: C901
    archive_path: Path,
    *,
    extract_to: Path | None = None,
    verify_archive_crc: bool = False,
) -> _ValidatedNativeArchive:
    """Validate ZIP structure and optionally read members for CRC verification or extraction."""
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
                infos = archive.infolist()
                for info in infos:
                    if not info.filename.startswith("filestore/"):
                        continue
                    member = info.filename
                    if "\\" in member or member.startswith("/") or posixpath.normpath(member) != member.rstrip("/"):
                        message = f"Odoo runtime archive contains unsafe filestore path: {member}"
                        raise RuntimeRestoreError(message)
                members = [info for info in infos if not info.is_dir()]
                if extract_to is not None and not verify_archive_crc:
                    members = [
                        info
                        for info in members
                        if info.filename == "dump.sql" or info.filename.startswith("filestore/")
                    ]
                elif extract_to is None and not verify_archive_crc:
                    members = []
                extracting_members = extract_to is not None and not verify_archive_crc
                if extracting_members:
                    LOGGER.info(
                        "Validating archive structure and identity; extracting dump and filestore with ZIP CRC checks."
                    )
                progress_label = (
                    "Extracting runtime archive" if extracting_members else "Validating runtime archive CRCs"
                )
                complete_label = "Extracted runtime archive" if extracting_members else "Validated runtime archive CRCs"
                total_bytes = sum(info.file_size for info in members)
                read_bytes = 0
                next_report = time.monotonic() + PROGRESS_INTERVAL_SECONDS
                for info in members:
                    try:
                        destination: Path | None = None
                        if extract_to is not None:
                            if info.filename == "dump.sql":
                                destination = extract_to / "dump.sql"
                            elif info.filename.startswith("filestore/"):
                                destination = extract_to / info.filename
                        if destination is not None:
                            destination.parent.mkdir(parents=True, exist_ok=True)
                        output_context = destination.open("wb") if destination is not None else nullcontext(None)
                        with archive.open(info) as member, output_context as output:
                            while chunk := member.read(1024 * 1024):
                                if output is not None:
                                    output.write(chunk)
                                read_bytes += len(chunk)
                                if time.monotonic() >= next_report and total_bytes:
                                    LOGGER.info(
                                        "%s: %d%% (%d/%d bytes)",
                                        progress_label,
                                        read_bytes * 100 // total_bytes,
                                        read_bytes,
                                        total_bytes,
                                    )
                                    next_report = time.monotonic() + PROGRESS_INTERVAL_SECONDS
                    except zipfile.BadZipFile as error:
                        message = f"Odoo runtime archive contains corrupt member '{info.filename}': {archive_path}"
                        raise RuntimeRestoreError(message) from error
                if total_bytes:
                    LOGGER.info("%s: 100%% (%d/%d bytes)", complete_label, read_bytes, total_bytes)
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


def validate_native_runtime_archive(archive_path: Path, *, verify_archive_crc: bool = False) -> None:
    """Validate Odoo's ZIP structure and optionally scan every member's CRC."""
    _validate_native_runtime_archive(archive_path, verify_archive_crc=verify_archive_crc)


def _extract_native_runtime_archive(
    archive_path: Path,
    *,
    verify_archive_crc: bool = False,
) -> _ValidatedNativeArchive:
    """Validate a native archive and extract it to temporary storage."""
    extracted = tempfile.TemporaryDirectory(prefix="godoo-archive-")
    try:
        validated = _validate_native_runtime_archive(
            archive_path,
            extract_to=Path(extracted.name),
            verify_archive_crc=verify_archive_crc,
        )
    except BaseException:
        extracted.cleanup()
        raise
    return _ValidatedNativeArchive(validated.identity, extracted)


def _require_original_filestore(original_filestore: Path | None) -> None:
    """Require an existing source when an archive overlay is requested."""
    if original_filestore is None or original_filestore.is_dir():
        return
    message = f"Filestore directory does not exist: {original_filestore}"
    raise RuntimeRestoreError(message)


def _require_replaceable_runtime(
    *,
    connection: DBConnection,
    db_name: str,
    target_filestore: Path,
    force: bool,
) -> None:
    """Reject replacement unless the caller explicitly selected force."""
    nonempty_filestore = target_filestore.is_dir() and next(target_filestore.iterdir(), None) is not None
    if force or not (
        database_exists(connection.with_db("postgres"), db_name)
        or nonempty_filestore
        or (target_filestore.exists() and not target_filestore.is_dir())
    ):
        return
    message = f"Runtime '{db_name}' already exists; pass --force to replace its database and filestore."
    raise RuntimeRestoreError(message)


def _validate_native_runtime_archive(
    archive_path: Path,
    *,
    extract_to: Path | None = None,
    verify_archive_crc: bool = False,
) -> _ValidatedNativeArchive:
    """Validate a native archive while optionally reporting full CRC scan progress."""
    with _ProgressPhase("Validating runtime archive", heartbeat=False, logger=LOGGER):
        return _validate_native_runtime_archive_contents(
            archive_path,
            extract_to=extract_to,
            verify_archive_crc=verify_archive_crc,
        )


def _reuse_or_validate_archive(
    archive_path: Path,
    validated_archive: _ValidatedNativeArchive | None,
    *,
    require_same_identity: bool = False,
    verify_archive_crc: bool = False,
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
    return _validate_native_runtime_archive(archive_path, verify_archive_crc=verify_archive_crc)


@contextmanager
def _open_validated_archive(
    archive_path: Path,
    validated_archive: _ValidatedNativeArchive,
) -> Iterator[zipfile.ZipFile]:
    """Open the same archive file whose identity passed structure validation."""
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


def _overlay_staged_filestore(source: Path, destination: Path) -> None:
    """Write a preflight-extracted archive filestore over a staged copy."""
    if not source.is_dir():
        return
    for member in sorted(source.rglob("*")):
        if not member.is_file():
            continue
        target = destination / member.relative_to(source)
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
        shutil.copyfile(member, target)


def load_runtime_archive(  # noqa: C901
    *,
    connection: DBConnection,
    data_dir: Path,
    archive_path: Path,
    odoo_bin_path: Path,
    odoo_conf_path: Path | None = None,
    original_filestore: Path | None = None,
    force: bool = False,
    odoo_version: int | None = None,
    lifecycle_owner: str | None = None,
    keep_reset_baseline: bool = False,
    require_same_archive_identity: bool = False,
    verify_archive_crc: bool = False,
    runner: CommandRunner | None = None,
    database_creator: DatabaseCreator = create_database,
    database_cleaner: DatabaseCleaner = cleanup_database,
    database_swapper: DatabaseSwapper = swap_database,
    database_rollback: DatabaseRollback = rollback_database_swap,
    _validated_archive: _ValidatedNativeArchive | None = None,
) -> int:
    """Stage a ZIP restore and promote its database and filestore together."""
    db_name = connection.db_name
    with ExitStack() as resources:
        # A lifecycle caller owns a supplied validation token. This operation
        # only releases an extraction it created itself.
        # Validate replacement eligibility while the target runtime remains stable.
        if odoo_version is None:
            target_major = odoo_bin_get_version(odoo_bin_path.parent).major
            require_supported_odoo_major(target_major, odoo_bin_path.parent)
        else:
            target_major = require_supported_odoo_major(odoo_version, odoo_bin_path.parent)
        lock_names = (db_name, baseline_database_name(db_name)) if keep_reset_baseline else (db_name,)
        with runtime_locks(data_dir, *lock_names, odoo_conf_path=odoo_conf_path):
            if original_filestore is not None and _validated_archive is None:
                validated_archive = _extract_native_runtime_archive(
                    archive_path,
                    verify_archive_crc=verify_archive_crc,
                )
                resources.callback(validated_archive.cleanup)
            else:
                validated_archive = _reuse_or_validate_archive(
                    archive_path,
                    _validated_archive,
                    require_same_identity=require_same_archive_identity,
                    verify_archive_crc=verify_archive_crc,
                )
            _require_original_filestore(original_filestore)
            runtime_data = runtime_data_directory(data_dir, odoo_conf_path)
            target_filestore = runtime_filestore_path(runtime_data, db_name)
            _require_replaceable_runtime(
                connection=connection,
                db_name=db_name,
                target_filestore=target_filestore,
                force=force,
            )
            # Build an isolated database and filestore before either live artifact is replaced.
            staged_database = temporary_database_name("native_restore")
            extracted = validated_archive.extracted
            if extracted is None:
                extracted = tempfile.TemporaryDirectory(prefix="godoo-archive-")
                resources.callback(extracted.cleanup)
                with (
                    _ProgressPhase("Extracting runtime database dump", logger=LOGGER),
                    _open_validated_archive(archive_path, validated_archive) as source,
                ):
                    source.extract("dump.sql", extracted.name)
            with _open_validated_archive(archive_path, validated_archive) as source:
                for member in source.namelist():
                    if member.startswith("filestore/") and not member.endswith("/"):
                        source.extract(member, extracted.name)
            command = [
                "psql",
                "--quiet",
                "--file",
                str(Path(extracted.name) / "dump.sql"),
                *postgres_argv(connection, database=staged_database),
            ]
            staged_filestore = runtime_filestore_path(runtime_data, staged_database)
            marker = None
            try:
                if original_filestore is not None:
                    assert extracted is not None
                    with _ProgressPhase("Copying original filestore", heartbeat=False, logger=LOGGER):
                        copy_filestore(original_filestore, staged_filestore)
                    with _ProgressPhase("Overlaying archive filestore", logger=LOGGER):
                        _overlay_staged_filestore(Path(extracted.name) / "filestore", staged_filestore)
                database_creator(connection.with_db(staged_database), "template0")
                result = 0
                with _ProgressPhase("Restoring runtime database", heartbeat=False, logger=LOGGER) as progress:
                    result = runner(command) if runner is not None else _run_postgres_command(command, connection)
                    progress.failed = result != 0
                if result:
                    return result
                with _ProgressPhase("Validating restored runtime", logger=LOGGER):
                    _validate_staged_base_version(connection.with_db(staged_database), target_major)
                if original_filestore is None:
                    source_filestore = Path(extracted.name) / "filestore"
                    if source_filestore.is_dir():
                        shutil.copytree(source_filestore, staged_filestore)
                    else:
                        staged_filestore.mkdir(parents=True, exist_ok=True)
                if keep_reset_baseline:
                    save_runtime_baseline(
                        connection=connection,
                        source_database=staged_database,
                        source_filestore=staged_filestore,
                        data_dir=runtime_data,
                    )
                marker = begin_runtime_restore(runtime_data, db_name, staged_database)
                backup_database = database_swapper(connection, staged_database)
                with _ProgressPhase("Promoting restored runtime", logger=LOGGER):
                    complete_runtime_promotion(
                        connection=connection,
                        marker=marker,
                        backup_database=backup_database,
                        staged_filestore=staged_filestore,
                        target_filestore=target_filestore,
                        database_cleaner=database_cleaner,
                        database_rollback=database_rollback,
                        filestore_replacer=replace_filestore,
                        lifecycle_owner=lifecycle_owner,
                    )
                return 0
            finally:
                pending = marker is not None and marker.exists()
                if not pending:
                    database_cleaner(connection.with_db(staged_database))
                else:
                    LOGGER.warning(
                        "Retaining native restore database %s because promotion remains pending", staged_database
                    )
                if staged_filestore.exists() and not pending:
                    try:
                        shutil.rmtree(staged_filestore)
                    except OSError:
                        LOGGER.warning(
                            "Could not remove native restore staging filestore %s", staged_filestore, exc_info=True
                        )
                elif staged_filestore.exists():
                    LOGGER.warning(
                        "Retaining native restore staging filestore %s because promotion remains pending",
                        staged_filestore,
                    )


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
    connection: DBConnection,
    data_dir: Path,
    source_folder: Path,
    db_template: str,
    force: bool = False,
    keep_reset_baseline: bool = False,
) -> None:
    """Load a directory emitted by gOdoo 0.17's ``backup dump`` command."""
    restore_custom_runtime(
        connection=connection,
        data_dir=data_dir,
        db_template=db_template,
        dump_path=source_folder / LEGACY_DUMP_FILENAME,
        filestore_source=_legacy_filestore_source(source_folder, connection.db_name),
        force=force,
        keep_reset_baseline=keep_reset_baseline,
    )
