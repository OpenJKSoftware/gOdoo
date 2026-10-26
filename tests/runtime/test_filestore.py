"""Tests for disposable filestore staging and archive overlays."""

from __future__ import annotations

import logging
import os
import select
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import IO, Literal

import pytest

import godoo_cli.helpers.progress as progress_reporting
import godoo_cli.runtime.archive as runtime_archive
from godoo_cli.database.connection import DBConnection
from godoo_cli.runtime.archive import (
    RuntimeRestoreError,
    _ProgressPhase,
    _reuse_or_validate_archive,
    _run_postgres_command,
    _validate_native_runtime_archive,
    _validate_staged_base_version,
    load_runtime_archive,
    prepare_archive_with_original_filestore,
    validate_native_runtime_archive,
)
from godoo_cli.runtime.filestore import _copy_command, _copy_with_rsync, copy_filestore


def _fake_progress_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, psql_status: int, pv_status: int, pv_delay: float = 0
) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    monkeypatch.setattr(runtime_archive.sys.stderr, "isatty", lambda: True)
    monkeypatch.setenv("GODOO_PV_ARGS", str(tools / "pv-args"))
    pv = tools / "pv"
    pv.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "$GODOO_PV_ARGS"\n'
        f'cat "$4"\nprintf "12%%\\r100%%\\n" >&2\nsleep {pv_delay}\nexit {pv_status}\n',
        encoding="utf-8",
    )
    psql = tools / "psql"
    psql.write_text(
        f"#!/bin/sh\ncat >/dev/null\necho setval\necho 1\necho fake psql diagnostic >&2\nexit {psql_status}\n",
        encoding="utf-8",
    )
    pv.chmod(0o755)
    psql.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    return tools


def test_pv_failure_prevents_sql_restore_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = _fake_progress_tools(tmp_path, monkeypatch, psql_status=0, pv_status=7)
    dump = tmp_path / "dump.sql"
    dump.write_text("select 1;", encoding="utf-8")

    result = _run_postgres_command(
        ["psql", "--quiet", "--file", str(dump), "--dbname", "staged"],
        DBConnection("host", 5432, "user", "pass", "runtime"),
    )

    assert result == 7
    assert (tools / "pv-args").read_text(encoding="utf-8").splitlines() == [
        "--force",
        "--interval",
        "5",
        str(dump),
    ]


def test_pv_can_finish_after_psql_closes_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_progress_tools(tmp_path, monkeypatch, psql_status=0, pv_status=0, pv_delay=0.1)
    dump = tmp_path / "dump.sql"
    dump.write_text("select 1;", encoding="utf-8")

    result = _run_postgres_command(
        ["psql", "--quiet", "--file", str(dump), "--dbname", "staged"],
        DBConnection("host", 5432, "user", "pass", "runtime"),
    )

    assert result == 0


@pytest.mark.parametrize("use_pv", [False, True])
@pytest.mark.parametrize("psql_status", [0, 8])
def test_sql_restore_suppresses_results_and_preserves_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    use_pv: bool,
    psql_status: int,
) -> None:
    _fake_progress_tools(tmp_path, monkeypatch, psql_status=psql_status, pv_status=0)
    if not use_pv:
        monkeypatch.setattr(runtime_archive.shutil, "which", lambda _command: None)
    dump = tmp_path / "dump.sql"
    dump.write_text("select 1;", encoding="utf-8")

    result = _run_postgres_command(
        ["psql", "--quiet", "--file", str(dump), "--dbname", "staged"],
        DBConnection("host", 5432, "user", "pass", "runtime"),
    )

    captured = capfd.readouterr()
    assert result == psql_status
    assert captured.out == ""
    assert "fake psql diagnostic" in captured.err


def test_archive_progress_phase_reports_heartbeat_and_stops_after_failure(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reports a blocking archive phase and leaves no heartbeat worker behind."""
    caplog.set_level(logging.INFO, logger=progress_reporting.LOGGER.name)
    heartbeat_seen = threading.Event()
    logger_info = progress_reporting.LOGGER.info

    def report_progress(message: str, *args: object) -> None:
        if "still running" in message:
            heartbeat_seen.set()
        logger_info(message, *args)

    monkeypatch.setattr(progress_reporting.LOGGER, "info", report_progress)
    assert _ProgressPhase("Archive progress").heartbeat_seconds == 10.0

    completed = _ProgressPhase("Completed archive progress", heartbeat_seconds=0.01)
    with completed:
        assert heartbeat_seen.wait(0.5)

    phase = _ProgressPhase("Testing archive progress", heartbeat_seconds=0.01)

    message = "restore failed"
    with pytest.raises(RuntimeError, match=message), phase:
        raise RuntimeError(message)

    assert not completed._thread.is_alive()
    assert not phase._thread.is_alive()
    messages = [record.getMessage() for record in caplog.records]
    assert any("Completed archive progress completed" in message for message in messages)
    assert "Testing archive progress started" in messages
    assert any("Completed archive progress still running" in message for message in messages)
    assert any("Testing archive progress failed" in message for message in messages)


def test_copy_command_selects_platform_specific_fast_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "stage"

    source.mkdir()
    (source / "attachment").write_text("data")
    assert _copy_command(source, destination, "darwin") == ["cp", "-cRLp", str(source / "attachment"), str(destination)]
    assert _copy_command(source, destination, "linux") == [
        "cp",
        "-RLp",
        "--reflink=always",
        f"{source}/.",
        str(destination),
    ]
    assert _copy_command(source, destination, "win32") is None


def test_portable_copy_creates_independent_materialized_files(tmp_path: Path) -> None:
    source = tmp_path / "original"
    source.mkdir()
    (source / "kept").write_text("original")
    alias = source / "alias"
    alias.symlink_to(source / "kept")

    destination = tmp_path / "stage"
    copy_filestore(source, destination, platform_name="portable")

    assert (destination / "kept").read_text() == "original"
    assert not (destination / "kept").is_symlink()
    assert not (destination / "alias").is_symlink()
    (destination / "kept").write_text("changed")
    assert (source / "kept").read_text() == "original"


@pytest.mark.skipif(sys.platform != "linux", reason="requires GNU cp")
def test_linux_fast_copy_places_source_contents_at_stage_root(tmp_path: Path) -> None:
    source = tmp_path / "original"
    source.mkdir()
    (source / "blob").write_text("attachment")
    destination = tmp_path / "stage"

    copy_filestore(source, destination, platform_name="linux")

    assert (destination / "blob").read_text() == "attachment"
    assert not (destination / source.name).exists()


def test_failed_fast_copy_discards_partial_stage_before_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "original"
    source.mkdir()
    (source / "kept").write_text("original")
    destination = tmp_path / "stage"
    calls = 0

    def fail_copy(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        (destination / "partial").write_text("partial")
        return subprocess.CompletedProcess([], 1, "", "copy failed")

    monkeypatch.setattr("godoo_cli.runtime.filestore.shutil.which", lambda _command: "/usr/bin/cp")
    monkeypatch.setattr("godoo_cli.runtime.filestore._clone_preflight", lambda *_args: None)
    monkeypatch.setattr("godoo_cli.runtime.filestore.subprocess.run", fail_copy)
    monkeypatch.setattr(
        "godoo_cli.runtime.filestore._copy_with_rsync", lambda *_args: (False, "rsync is not available")
    )

    copy_filestore(source, destination, platform_name="linux")

    assert calls == 1
    assert sorted(path.name for path in destination.iterdir()) == ["kept"]


def _fake_rsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, status: int) -> Path:
    """Provide a small rsync substitute with controllable progress and status."""
    tools = tmp_path / "tools"
    tools.mkdir()
    rsync = tools / "rsync"
    rsync.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$@" > "$GODOO_RSYNC_ARGS"\n'
        'if [ "$3" = "--info=progress2" ]; then\n'
        "    printf '12%%\\rdiagnostic one\\n34%%\\rdiagnostic two\\n56%%\\r78%%\\n'\n"
        "    shift\n"
        "fi\n"
        "printf 'rsync diagnostic\\n' >&2\n"
        f"if [ {status} -ne 0 ]; then\n"
        '    mkdir -p "$4"\n'
        '    printf stale > "$4/stale"\n'
        f"    exit {status}\n"
        "fi\n"
        'cp -RL "$3/." "$4"\n'
        f"exit {status}\n",
        encoding="utf-8",
    )
    rsync.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GODOO_RSYNC_ARGS", str(tmp_path / "rsync-args"))
    return rsync


@pytest.mark.parametrize("interactive", [False, True])
def test_rsync_copy_passes_output_directly_to_stdout_and_materializes_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capfdbinary: pytest.CaptureFixture[bytes],
    interactive: bool,
) -> None:
    monkeypatch.setattr("godoo_cli.runtime.filestore.sys.stdout.isatty", lambda: interactive)
    monkeypatch.setattr("godoo_cli.runtime.filestore.sys.stderr.isatty", lambda: not interactive)
    source = tmp_path / "source"
    source.mkdir()
    (source / "kept").write_text("original")
    (source / "alias").symlink_to(source / "kept")
    destination = tmp_path / "stage"
    destination.mkdir()
    _fake_rsync(tmp_path, monkeypatch, status=0)
    caplog.set_level(logging.INFO, logger="godoo_cli.runtime.filestore")

    copied, error = _copy_with_rsync(source, destination)

    assert (copied, error) == (True, None)
    assert (destination / "kept").read_text() == "original"
    assert (destination / "alias").read_text() == "original"
    assert not (destination / "alias").is_symlink()
    assert not (destination / source.name).exists()
    assert (tmp_path / "rsync-args").read_text().splitlines() == [
        "-rLpt",
        "--human-readable",
        *(["--info=progress2"] if interactive else []),
        f"{source}/",
        str(destination),
    ]
    assert "-a" not in (tmp_path / "rsync-args").read_text().splitlines()
    captured = capfdbinary.readouterr()
    progress = b"12%\rdiagnostic one\n34%\rdiagnostic two\n56%\r78%\n" if interactive else b""
    assert captured.out == progress + b"rsync diagnostic\n"
    assert captured.err == b""
    assert not caplog.records


def test_failed_rsync_discards_partial_stage_before_python_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "kept").write_text("original")
    destination = tmp_path / "stage"
    rsync = _fake_rsync(tmp_path, monkeypatch, status=9)

    def fail_copy(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        (destination / "clone-partial").write_text("partial")
        return subprocess.CompletedProcess([], 1, "", "copy failed")

    monkeypatch.setattr(
        "godoo_cli.runtime.filestore.shutil.which",
        lambda command: "/usr/bin/cp" if command == "cp" else str(rsync),
    )
    monkeypatch.setattr("godoo_cli.runtime.filestore.subprocess.run", fail_copy)

    copy_filestore(source, destination, platform_name="linux")

    assert (destination / "kept").read_text() == "original"
    assert not (destination / "clone-partial").exists()
    assert not (destination / "stale").exists()


def test_archive_rejects_traversing_filestore_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
        archive.writestr("filestore/../../outside", "bad")

    with pytest.raises(RuntimeRestoreError, match="unsafe filestore path"):
        validate_native_runtime_archive(archive_path)


@pytest.mark.parametrize("extract_during_validation", [True, False])
def test_original_filestore_restore_uses_staged_sql_and_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extract_during_validation: bool
) -> None:
    data_dir = tmp_path / "data"
    source = tmp_path / "original"
    source.mkdir()
    (source / "retained").write_text("old")
    (source / "collision").write_text("old")
    (source / "replaced-directory").write_text("old")
    archive_path = tmp_path / "upgraded.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
        archive.writestr("filestore/collision", "new")
        archive.writestr("filestore/addition", "added")
        archive.writestr("filestore/replaced-directory/nested", "new")
    original_open = zipfile.ZipFile.open
    payload_reads: list[str] = []

    def count_payload_reads(
        archive: zipfile.ZipFile,
        name: str | zipfile.ZipInfo,
        mode: Literal["r", "w"] = "r",
        pwd: bytes | None = None,
        *,
        force_zip64: bool = False,
    ) -> IO[bytes]:
        payload_reads.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original_open(archive, name, mode, pwd, force_zip64=force_zip64)

    monkeypatch.setattr(zipfile.ZipFile, "open", count_payload_reads)

    monkeypatch.setattr("godoo_cli.runtime.archive.database_exists", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("godoo_cli.runtime.archive._validate_staged_base_version", lambda *_args: None)
    commands: list[list[str]] = []
    saved: list[Path] = []

    def save_baseline(**kwargs: object) -> None:
        source_filestore = kwargs["source_filestore"]
        assert isinstance(source_filestore, Path)
        assert (source_filestore / "collision").read_text() == "new"
        assert (source_filestore / "addition").read_text() == "added"
        assert not (data_dir / "filestore" / "runtime").exists()
        saved.append(source_filestore)

    monkeypatch.setattr("godoo_cli.runtime.archive.save_runtime_baseline", save_baseline)
    connection = DBConnection("host", 5432, "user", "pass", "runtime")
    validated_archive = (
        prepare_archive_with_original_filestore(archive_path, source)
        if extract_during_validation
        else _reuse_or_validate_archive(archive_path, None)
    )
    result = load_runtime_archive(
        connection=connection,
        data_dir=data_dir,
        archive_path=archive_path,
        original_filestore=source,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        force=True,
        keep_reset_baseline=True,
        odoo_version=19,
        runner=lambda command: commands.append(list(command)) or 0,
        _validated_archive=validated_archive,
        database_creator=lambda *_args: None,
        database_swapper=lambda *_args: None,
        database_rollback=lambda *_args: None,
        database_cleaner=lambda *_args: None,
    )

    target = data_dir / "filestore" / "runtime"
    assert result == 0
    assert len(saved) == 1
    assert payload_reads == [
        "dump.sql",
        "filestore/collision",
        "filestore/addition",
        "filestore/replaced-directory/nested",
    ]
    assert commands[0][0] == "psql"
    assert commands[0][1] == "--quiet"
    assert (target / "retained").read_text() == "old"
    assert (target / "collision").read_text() == "new"
    assert (target / "addition").read_text() == "added"
    assert (target / "replaced-directory" / "nested").read_text() == "new"
    (target / "retained").write_text("changed")
    assert (source / "retained").read_text() == "old"


def test_original_filestore_preflight_failure_preserves_caller_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected replacement leaves the supplied archive for caller cleanup."""
    data_dir = tmp_path / "data"
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "active").write_text("keep")
    source = tmp_path / "original"
    source.mkdir()
    archive_path = tmp_path / "upgraded.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")

    validated = prepare_archive_with_original_filestore(archive_path, source)
    assert validated is not None
    assert validated.extracted is not None
    extracted_path = Path(validated.extracted.name)
    monkeypatch.setattr("godoo_cli.runtime.archive.database_exists", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeRestoreError, match="already exists"):
        load_runtime_archive(
            connection=DBConnection("host", 5432, "user", "pass", "runtime"),
            data_dir=data_dir,
            archive_path=archive_path,
            original_filestore=source,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            odoo_version=19,
            _validated_archive=validated,
        )

    assert extracted_path.exists()
    validated.cleanup()
    assert not extracted_path.exists()


def test_original_filestore_database_probe_failure_preserves_caller_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Database preflight errors release the extraction held by the caller token."""
    source = tmp_path / "original"
    source.mkdir()
    archive_path = tmp_path / "upgraded.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")

    validated = prepare_archive_with_original_filestore(archive_path, source)
    assert validated is not None
    assert validated.extracted is not None
    extracted_path = Path(validated.extracted.name)

    def unavailable(*_args: object, **_kwargs: object) -> bool:
        message = "PostgreSQL unavailable"
        raise OSError(message)

    monkeypatch.setattr("godoo_cli.runtime.archive.database_exists", unavailable)
    with pytest.raises(OSError, match="unavailable"):
        load_runtime_archive(
            connection=DBConnection("host", 5432, "user", "pass", "runtime"),
            data_dir=tmp_path / "data",
            archive_path=archive_path,
            original_filestore=source,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            odoo_version=19,
            _validated_archive=validated,
        )

    assert extracted_path.exists()
    validated.cleanup()
    assert not extracted_path.exists()


@pytest.mark.parametrize("replacement", [False, True])
def test_original_filestore_identity_failure_preserves_caller_archive(tmp_path: Path, replacement: bool) -> None:
    """Missing and replaced archives release the extraction before database work."""
    source = tmp_path / "original"
    source.mkdir()
    archive_path = tmp_path / "upgraded.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")

    validated = prepare_archive_with_original_filestore(archive_path, source)
    assert validated is not None
    assert validated.extracted is not None
    extracted_path = Path(validated.extracted.name)
    if replacement:
        with zipfile.ZipFile(tmp_path / "replacement.zip", "w") as archive:
            archive.writestr("dump.sql", "select 2")
        (tmp_path / "replacement.zip").replace(archive_path)
    else:
        archive_path.unlink()

    with pytest.raises(RuntimeRestoreError, match="changed after lifecycle plan validation"):
        load_runtime_archive(
            connection=DBConnection("host", 5432, "user", "pass", "runtime"),
            data_dir=tmp_path / "data",
            archive_path=archive_path,
            original_filestore=source,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            odoo_version=19,
            require_same_archive_identity=True,
            _validated_archive=validated,
        )

    assert extracted_path.exists()
    validated.cleanup()
    assert not extracted_path.exists()


def test_original_filestore_cancellation_preserves_caller_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interrupted staged restores discard their preflight archive extraction."""
    data_dir = tmp_path / "data"
    source = tmp_path / "original"
    source.mkdir()
    archive_path = tmp_path / "upgraded.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")

    validated = prepare_archive_with_original_filestore(archive_path, source)
    assert validated is not None
    assert validated.extracted is not None
    extracted_path = Path(validated.extracted.name)
    monkeypatch.setattr("godoo_cli.runtime.archive.database_exists", lambda *_args, **_kwargs: False)

    def cancel_restore(_command: list[str]) -> int:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        load_runtime_archive(
            connection=DBConnection("host", 5432, "user", "pass", "runtime"),
            data_dir=data_dir,
            archive_path=archive_path,
            original_filestore=source,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            force=True,
            odoo_version=19,
            runner=cancel_restore,
            _validated_archive=validated,
            database_creator=lambda *_args: None,
            database_cleaner=lambda *_args: None,
        )

    assert extracted_path.exists()
    validated.cleanup()
    assert not extracted_path.exists()


def test_failed_staged_sql_restore_preserves_active_filestore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    waiting = threading.Event()
    logger_info = runtime_archive.LOGGER.info

    def report_wait(message: str, *args: object) -> None:
        if "still running" in message and args[0] == "Restoring runtime database":
            waiting.set()
        logger_info(message, *args)

    monkeypatch.setattr(runtime_archive.LOGGER, "info", report_wait)
    monkeypatch.setattr(runtime_archive, "_ProgressPhase", partial(_ProgressPhase, heartbeat_seconds=0.01))
    data_dir = tmp_path / "data"
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "active").write_text("keep")
    source = tmp_path / "original"
    source.mkdir()
    (source / "retained").write_text("old")
    archive_path = tmp_path / "upgraded.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
        archive.writestr("filestore/addition", "new")

    def fail_restore(command: list[str], dump_path: Path, env: dict[str, str]) -> int:
        assert command[:3] == ["psql", "--quiet", "--file"]
        assert command[3] == "-"
        assert dump_path.is_file()
        assert command[command.index("--host") + 1] == "host"
        assert command[command.index("--username") + 1] == "user"
        assert command[command.index("--dbname") + 1] != "runtime"
        assert "pass" not in command
        assert env["PGPASSWORD"] == "pass"
        assert Path(env["PSQLRC"]).read_text() == "\\set ON_ERROR_STOP on\n"
        assert not waiting.wait(0.05), "Database restore emitted a redundant heartbeat"
        return 1

    monkeypatch.setattr("godoo_cli.runtime.archive.database_exists", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runtime_archive.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runtime_archive, "_stream_sql_input", fail_restore)
    result = load_runtime_archive(
        connection=DBConnection("host", 5432, "user", "pass", "runtime"),
        data_dir=data_dir,
        archive_path=archive_path,
        original_filestore=source,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        force=True,
        odoo_version=19,
        database_creator=lambda *_args: None,
        database_cleaner=lambda *_args: None,
    )

    assert result == 1
    assert sorted(path.name for path in target.iterdir()) == ["active"]
    assert (target / "active").read_text() == "keep"
    assert (source / "retained").read_text() == "old"
    assert not list((data_dir / "filestore").glob("godoo_native_restore_*"))
    messages = [record.getMessage() for record in caplog.records]
    assert any("Restoring runtime database failed" in message for message in messages)
    assert not any("Restoring runtime database completed" in message for message in messages)


def test_replaced_archive_is_validated_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
    original_open = zipfile.ZipFile.open
    crc_reads = 0

    def count_crc_reads(archive: zipfile.ZipFile, name: str | zipfile.ZipInfo, *args: object, **kwargs: object):
        nonlocal crc_reads
        if getattr(name, "filename", name) == "dump.sql":
            crc_reads += 1
        return original_open(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", count_crc_reads)
    validated = _validate_native_runtime_archive(archive_path, verify_archive_crc=True)
    replacement_path = tmp_path / "replacement.zip"
    with zipfile.ZipFile(replacement_path, "w") as archive:
        archive.writestr("dump.sql", "select 2")
    replacement_path.replace(archive_path)

    refreshed = _reuse_or_validate_archive(archive_path, validated, verify_archive_crc=True)

    assert refreshed.identity != validated.identity
    assert crc_reads >= 2


def test_archive_crc_progress_uses_five_second_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", b"x" * (3 * 1024 * 1024))

    caplog.set_level(logging.INFO, logger=runtime_archive.LOGGER.name)
    monotonic_values = iter((0.0, 4.0, 5.0, 5.0, 6.0))
    monkeypatch.setattr(runtime_archive.time, "monotonic", lambda: next(monotonic_values))

    runtime_archive._validate_native_runtime_archive_contents(archive_path, verify_archive_crc=True)

    progress_messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Validating runtime archive CRCs:")
    ]
    assert progress_messages == ["Validating runtime archive CRCs: 66% (2097152/3145728 bytes)"]
    assert "Validated runtime archive CRCs: 100% (3145728/3145728 bytes)" in caplog.messages


def test_native_wrong_major_preserves_active_filestore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "active").write_text("keep")
    archive_path = tmp_path / "upgraded.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")

    @contextmanager
    def connect_to_wrong_major(_connection: DBConnection) -> Iterator[_VersionCursor]:
        yield _VersionCursor(("18.0.1.0.0",))

    monkeypatch.setattr(DBConnection, "connect", connect_to_wrong_major)
    swaps: list[bool] = []

    with pytest.raises(RuntimeRestoreError, match="version 18"):
        load_runtime_archive(
            connection=DBConnection("host", 5432, "user", "pass", "runtime"),
            data_dir=data_dir,
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            force=True,
            odoo_version=19,
            runner=lambda _command: 0,
            database_creator=lambda *_args: None,
            database_swapper=lambda *_args: swaps.append(True),
            database_rollback=lambda *_args: None,
            database_cleaner=lambda *_args: None,
        )

    assert (target / "active").read_text() == "keep"
    assert not swaps


class _VersionCursor:
    def __init__(self, row: tuple[str | None] | None) -> None:
        self.row = row
        self.query = ""

    def execute(self, query: str) -> None:
        self.query = query

    def fetchone(self) -> tuple[str | None] | None:
        return self.row


class _VersionConnection:
    def __init__(self, row: tuple[str | None] | None) -> None:
        self.cursor = _VersionCursor(row)

    @contextmanager
    def connect(self) -> Iterator[_VersionCursor]:
        yield self.cursor


def test_staged_base_version_must_match_runtime_major() -> None:
    connection = _VersionConnection(("19.0.1.0.0",))

    _validate_staged_base_version(connection, 19)  # type: ignore[arg-type]

    assert connection.cursor.query == "SELECT latest_version FROM ir_module_module WHERE name = 'base';"


@pytest.mark.parametrize("row", [None, (None,), ("unknown",), ("18.0.1.0.0",)])
def test_staged_base_version_rejects_missing_or_unusable_version(row: tuple[str | None] | None) -> None:
    with pytest.raises(RuntimeRestoreError):
        _validate_staged_base_version(_VersionConnection(row), 19)  # type: ignore[arg-type]


@pytest.mark.parametrize("base_row", [("18.0.1.0.0",), None, (None,)])
def test_invalid_staged_base_version_preserves_active_database_and_filestore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_row: tuple[str | None] | None,
) -> None:
    data_dir = tmp_path / "data"
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "active").write_text("keep")
    archive_path = tmp_path / "upgraded.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
        archive.writestr("filestore/new", "new")
    swaps: list[bool] = []
    cleanups: list[str] = []

    @contextmanager
    def connect_to_staged(_connection: DBConnection) -> Iterator[_VersionCursor]:
        yield _VersionCursor(base_row)

    monkeypatch.setattr(DBConnection, "connect", connect_to_staged)

    with pytest.raises(RuntimeRestoreError):
        load_runtime_archive(
            connection=DBConnection("host", 5432, "user", "pass", "runtime"),
            data_dir=data_dir,
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            force=True,
            odoo_version=19,
            runner=lambda _command: 0,
            database_creator=lambda *_args: None,
            database_swapper=lambda *_args: swaps.append(True),
            database_cleaner=lambda connection: cleanups.append(connection.db_name),
        )

    assert not swaps
    assert cleanups
    assert (target / "active").read_text() == "keep"
    assert not (target / "new").exists()


def test_archive_rejects_corrupt_member_crc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive_path = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
    original_open = zipfile.ZipFile.open

    def corrupt_dump(archive: zipfile.ZipFile, name: str | zipfile.ZipInfo, *args: object, **kwargs: object):
        if getattr(name, "filename", name) == "dump.sql":
            message = "corrupt dump"
            raise zipfile.BadZipFile(message)
        return original_open(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", corrupt_dump)

    with pytest.raises(RuntimeRestoreError, match=r"corrupt member 'dump\.sql'"):
        validate_native_runtime_archive(archive_path, verify_archive_crc=True)


def test_terminal_pv_progress_preserves_carriage_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    """Keep native progress and PostgreSQL diagnostics on stderr."""
    _fake_progress_tools(tmp_path, monkeypatch, psql_status=0, pv_status=0)
    dump = tmp_path / "dump.sql"
    dump.write_text("select 1;", encoding="utf-8")

    result = _run_postgres_command(
        ["psql", "--quiet", "--file", str(dump), "--dbname", "staged"],
        DBConnection("host", 5432, "user", "pass", "runtime"),
    )

    captured = capfdbinary.readouterr()
    assert result == 0
    assert b"12%\r100%\n" in captured.err
    assert b"fake psql diagnostic\n" in captured.err
    assert captured.out == b""


def test_piped_sql_progress_arrives_before_transfer_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deliver complete progress lines while a slow SQL consumer is running."""
    tools = _fake_progress_tools(tmp_path, monkeypatch, psql_status=0, pv_status=99)
    dump = tmp_path / "dump.sql"
    dump.write_bytes(b"select 1;\n" * (400 * 1024))
    captured_sql = tmp_path / "captured.sql"
    (tools / "psql").write_text(
        f"#!{sys.executable}\n"
        "import sys, time\n"
        f"with open({str(captured_sql)!r}, 'wb') as captured:\n"
        "    while chunk := sys.stdin.buffer.read(65536):\n"
        "        captured.write(chunk)\n"
        "        time.sleep(0.02)\n",
        encoding="utf-8",
    )
    wrapper_code = (
        "import logging, sys\n"
        "from pathlib import Path\n"
        "from godoo_cli.database.connection import DBConnection\n"
        "from godoo_cli.runtime import archive\n"
        "logging.basicConfig(level=logging.INFO)\n"
        "archive.PROGRESS_INTERVAL_SECONDS = 0.01\n"
        "sys.exit(archive._run_postgres_command(\n"
        "    ['psql', '--file', sys.argv[1]], DBConnection('', 0, '', '', 'test')\n"
        "))\n"
    )
    with subprocess.Popen(
        [sys.executable, "-c", wrapper_code, str(dump)], stderr=subprocess.PIPE, stdout=subprocess.PIPE
    ) as wrapper:
        assert wrapper.stderr is not None
        output = b""
        deadline = time.monotonic() + 5
        try:
            while b"1048576/4096000 bytes sent (25.6%)\n" not in output:
                remaining = deadline - time.monotonic()
                assert remaining > 0, output
                assert select.select([wrapper.stderr], [], [], remaining)[0], output
                chunk = os.read(wrapper.stderr.fileno(), 4096)
                assert chunk, output
                output += chunk
            assert wrapper.poll() is None
            stdout, remainder = wrapper.communicate(timeout=5)
        finally:
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait(timeout=5)

    assert wrapper.returncode == 0
    assert stdout == b""
    assert b"\r" not in output + remainder
    assert b"bytes sent (100.0%)\n" in remainder
    assert b"PostgreSQL restore completed\n" in remainder
    assert not (tools / "pv-args").exists()
    assert captured_sql.read_bytes() == dump.read_bytes()
