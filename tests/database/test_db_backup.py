"""Backup format, attachment retention, and publication contracts."""

import subprocess
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import psycopg2
import pytest
from typer.testing import CliRunner

from godoo_cli.commands.db import archive as command_archive
from godoo_cli.commands.root import main_cli
from godoo_cli.database.connection import DBConnection
from godoo_cli.models import OdooVersion
from godoo_cli.runtime import archive, backup
from godoo_cli.runtime.backup import BackupFormat


@pytest.fixture
def guard(monkeypatch: pytest.MonkeyPatch) -> Mock:
    cursor = Mock()
    cursor.fetchall.return_value = [(42,)]
    cursor.active = False

    @contextmanager
    def connect(_connection: DBConnection) -> Iterator[Mock]:
        cursor.active = True
        try:
            yield cursor
        finally:
            cursor.active = False

    monkeypatch.setattr(DBConnection, "connect", connect)
    return cursor


@pytest.mark.parametrize("major", [16, 17, 18, 19, 20])
@pytest.mark.parametrize("backup_format", list(BackupFormat))
def test_backup_captures_database_before_filestore(
    tmp_path: Path, major: int, backup_format: BackupFormat, guard: Mock
) -> None:
    data = tmp_path / "data"
    filestore = data / "filestore" / "runtime"
    (filestore / "ab").mkdir(parents=True)
    (filestore / "ab" / "snapshot-file").write_bytes(b"snapshot attachment")
    destination = tmp_path / ("dataset" if backup_format == BackupFormat.DUMP else "runtime.zip")
    calls: list[list[str]] = []

    def run(command: Sequence[str]) -> int:
        assert guard.active
        calls.append(list(command))
        if command[0] == "rsync":
            assert len(calls) == 2
            # Exercise the actual rsync command and trailing-slash layout.
            return subprocess.run(command, check=False).returncode
        if command[0] == "pg_dump":
            output = Path(command[command.index("--file") + 1])
            output.write_bytes(b"PGDMP" if backup_format == BackupFormat.DUMP else b"SELECT 1;")
        else:
            assert "--no-filestore" in command
            with zipfile.ZipFile(command[-1], "w") as native:
                native.writestr("dump.sql", "SELECT 1;")
                native.writestr("manifest.json", "{}")
        # An attachment committed during the dump must be included in the later copy.
        (filestore / "ab" / "new-file").write_bytes(b"new attachment")
        return 0

    assert (
        archive.dump_runtime_archive(
            db_name="runtime",
            archive_path=destination,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=data,
            odoo_version=major,
            backup_format=backup_format,
            runner=run,
        )
        == 0
    )
    assert not guard.active
    assert guard.execute.call_args.args == ("SELECT 1",)
    if backup_format == BackupFormat.DUMP:
        assert (destination / "odoo.dump").read_bytes() == b"PGDMP"
        copied = destination / "odoo_filestore" / "filestore" / "runtime"
        assert (copied / "ab" / "snapshot-file").read_bytes() == b"snapshot attachment"
        assert (copied / "ab" / "new-file").read_bytes() == b"new attachment"
        assert archive._legacy_filestore_source(destination, "restored_name") == copied
    else:
        with zipfile.ZipFile(destination) as result:
            assert result.read("dump.sql") == b"SELECT 1;"
            assert result.read("filestore/ab/snapshot-file") == b"snapshot attachment"
            assert result.read("filestore/ab/new-file") == b"new attachment"
            if major >= 19:
                assert result.read("manifest.json") == b"{}"
        archive.validate_native_runtime_archive(destination)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["data", destination.name]


@pytest.mark.parametrize("failure", ["dump", "rsync", "guard"])
def test_failed_directory_backup_preserves_previous_pair(tmp_path: Path, guard: Mock, failure: str) -> None:
    destination = tmp_path / "dataset"
    old_filestore = destination / "odoo_filestore" / "filestore" / "runtime"
    old_filestore.mkdir(parents=True)
    (old_filestore / "old").write_bytes(b"old attachment")
    (destination / "odoo.dump").write_bytes(b"old dump")
    filestore = tmp_path / "data" / "filestore" / "runtime"
    filestore.mkdir(parents=True)
    (filestore / "new").write_bytes(b"new attachment")

    def execute(query: str) -> None:
        if query == "SELECT 1" and failure == "guard":
            message = "guard connection expired"
            raise psycopg2.OperationalError(message)

    guard.execute.side_effect = execute

    def run(command: Sequence[str]) -> int:
        if command[0] == "pg_dump":
            Path(command[command.index("--file") + 1]).write_bytes(b"partial or new dump")
            return 7 if failure == "dump" else 0
        Path(command[-1], "partial").write_bytes(b"partial copy")
        return 23 if failure == "rsync" else 0

    arguments = {
        "db_name": "runtime",
        "archive_path": destination,
        "odoo_bin_path": Path("/odoo/odoo-bin"),
        "data_dir": tmp_path / "data",
        "odoo_version": 19,
        "backup_format": BackupFormat.DUMP,
        "runner": run,
    }
    if failure == "guard":
        with pytest.raises(psycopg2.OperationalError, match="expired"):
            archive.dump_runtime_archive(**arguments)
    else:
        assert archive.dump_runtime_archive(**arguments) == (7 if failure == "dump" else 23)
    assert (destination / "odoo.dump").read_bytes() == b"old dump"
    assert (old_filestore / "old").read_bytes() == b"old attachment"
    assert not guard.active
    assert not list(tmp_path.glob(".*.tmp"))


def test_cleanup_guard_uses_only_the_standard_cron_row(guard: Mock) -> None:
    with backup.retain_attachment_files(DBConnection("", 0, "", "", "runtime")):
        query = guard.execute.call_args.args[0]
        assert "base" in query
        assert "autovacuum_job" in query
        assert "FOR NO KEY UPDATE OF cron NOWAIT" in query
        assert "ir_attachment" not in query
    assert not guard.active


@pytest.mark.parametrize("busy", [False, True])
def test_cleanup_guard_fails_when_missing_or_busy(guard: Mock, busy: bool) -> None:
    guard.fetchall.return_value = []
    if busy:
        guard.execute.side_effect = psycopg2.errors.LockNotAvailable("busy")
    with (
        pytest.raises(RuntimeError, match="running" if busy else "missing"),
        backup.retain_attachment_files(DBConnection("", 0, "", "", "runtime")),
    ):
        pytest.fail("backup must not start without attachment retention")
    assert not guard.active


@pytest.mark.parametrize("fail_promotion", [False, True])
def test_directory_promotion_replaces_or_restores_the_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_promotion: bool
) -> None:
    destination, staged = tmp_path / "dataset", tmp_path / "staged"
    for path, content in [(destination, b"previous"), (staged, b"new")]:
        path.mkdir()
        (path / "odoo.dump").write_bytes(content)
        (path / "odoo_filestore").mkdir()
        (path / "odoo_filestore" / "attachment").write_bytes(content)
    replace = backup.os.replace

    def promote(source: Path, target: Path) -> None:
        if source == staged and fail_promotion:
            message = "promotion failed"
            raise OSError(message)
        replace(source, target)

    monkeypatch.setattr(backup.os, "replace", promote)
    if fail_promotion:
        with pytest.raises(OSError, match="promotion failed"):
            backup.publish_backup_directory(staged, destination)
    else:
        backup.publish_backup_directory(staged, destination)
    expected = b"previous" if fail_promotion else b"new"
    assert (destination / "odoo.dump").read_bytes() == expected
    assert (destination / "odoo_filestore" / "attachment").read_bytes() == expected
    assert not (tmp_path / ".dataset.previous").exists()


def test_directory_promotion_preserves_interrupted_previous_pair(tmp_path: Path) -> None:
    previous = tmp_path / ".dataset.previous"
    previous.mkdir()
    (previous / "odoo.dump").write_bytes(b"previous")
    with pytest.raises(RuntimeError, match="recovery is required"):
        backup.publish_backup_directory(tmp_path / "staged", tmp_path / "dataset")
    assert (previous / "odoo.dump").read_bytes() == b"previous"


@pytest.mark.parametrize(
    ("option", "environment", "expected"),
    [
        ([], None, BackupFormat.ZIP),
        (["--format", "dump"], None, BackupFormat.DUMP),
        ([], "dump", BackupFormat.DUMP),
        (["--format", "zip"], "dump", BackupFormat.ZIP),
    ],
)
@pytest.mark.parametrize("status", [0, 7])
def test_cli_selects_backup_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: list[str],
    environment: str | None,
    expected: BackupFormat,
    status: int,
) -> None:
    monkeypatch.delenv("GODOO_BACKUP_FORMAT", raising=False)
    if environment is not None:
        monkeypatch.setenv("GODOO_BACKUP_FORMAT", environment)
    monkeypatch.setattr(command_archive, "resolve_development_odoo_main_path", lambda path: path)
    monkeypatch.setattr(command_archive, "require_cli_odoo_version", lambda *_args: OdooVersion("Odoo", 19, 0))
    dump = Mock(return_value=status)
    monkeypatch.setattr(command_archive, "dump_runtime_archive", dump)
    result = CliRunner().invoke(
        main_cli(),
        [
            "db",
            "backup",
            str(tmp_path / "output"),
            "--db-name",
            "runtime",
            "--odoo-main-path",
            "/odoo",
            *option,
        ],
    )
    assert result.exit_code == status, result.output
    assert dump.call_args.kwargs["backup_format"] == expected


def test_cli_rejects_unknown_backup_format(tmp_path: Path) -> None:
    result = CliRunner().invoke(main_cli(), ["db", "backup", str(tmp_path), "--format", "sql"])
    assert result.exit_code == 2
    assert "Invalid value" in result.output


@pytest.mark.parametrize("backup_format", list(BackupFormat))
def test_backup_rejects_destination_inside_runtime_storage(
    tmp_path: Path, backup_format: BackupFormat, guard: Mock
) -> None:
    runner = Mock()
    with pytest.raises(ValueError, match="outside Odoo's data directory"):
        archive.dump_runtime_archive(
            db_name="runtime",
            archive_path=tmp_path / "data" / "filestore" / "runtime" / "backup.zip",
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=tmp_path / "data",
            odoo_version=19,
            backup_format=backup_format,
            runner=runner,
        )
    runner.assert_not_called()
    guard.execute.assert_not_called()


def test_dump_backup_rejects_existing_file_destination(tmp_path: Path, guard: Mock) -> None:
    destination = tmp_path / "backup.zip"
    destination.write_bytes(b"existing ZIP backup")
    runner = Mock()

    with pytest.raises(ValueError, match="dump backup destination must be a directory"):
        archive.dump_runtime_archive(
            db_name="runtime",
            archive_path=destination,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=tmp_path / "data",
            odoo_version=19,
            backup_format=BackupFormat.DUMP,
            runner=runner,
        )

    assert destination.read_bytes() == b"existing ZIP backup"
    assert not destination.with_name(f".{destination.name}.previous").exists()
    runner.assert_not_called()
    guard.execute.assert_not_called()


@pytest.mark.parametrize(
    ("major", "backup_format"), [(16, BackupFormat.ZIP), (19, BackupFormat.ZIP), (19, BackupFormat.DUMP)]
)
def test_backup_subprocess_uses_the_guarded_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: Mock, major: int, backup_format: BackupFormat
) -> None:
    connection = DBConnection("selected-host", 5433, "selected-user", "selected-password", "runtime", sslmode="require")
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGSSLMODE"):
        monkeypatch.setenv(name, "stale-value")
    observed: list[list[str]] = []

    def run(command: Sequence[str], *, env: dict[str, str]) -> subprocess.CompletedProcess:
        assert guard.active
        observed.append(list(command))
        assert env["PGPASSWORD"] == connection.password
        assert env["PGSSLMODE"] == "require"
        assert connection.password not in command
        if command[0] == "pg_dump":
            assert "stale-value" not in env.values()
            assert command[command.index("--host") + 1] == connection.hostname
            assert command[command.index("--port") + 1] == str(connection.port)
            assert command[command.index("--username") + 1] == connection.username
            Path(command[command.index("--file") + 1]).write_bytes(b"database")
        else:
            assert env["PGHOST"] == connection.hostname
            assert env["PGPORT"] == str(connection.port)
            assert env["PGUSER"] == connection.username
            with zipfile.ZipFile(command[-1], "w") as native:
                native.writestr("dump.sql", "database")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(archive, "run_odoo_command", run)
    assert (
        archive.dump_runtime_archive(
            db_name="runtime",
            archive_path=tmp_path / "backup",
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=tmp_path / "data",
            odoo_version=major,
            backup_format=backup_format,
            connection=connection,
        )
        == 0
    )
    assert len(observed) == 1
