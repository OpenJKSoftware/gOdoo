"""Tests for guarded custom PostgreSQL runtime restoration."""

from pathlib import Path

import pytest

from godoo_cli.database.connection import DBConnection
from godoo_cli.runtime.locks import runtime_restore_marker
from godoo_cli.runtime.restore import RuntimeRestoreError, restore_custom_runtime, runtime_filestore_path


def _connection() -> DBConnection:
    return DBConnection("db", 5432, "odoo", "secret", "runtime")


@pytest.fixture(autouse=True)
def absent_runtime_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep restore behavior tests independent of a PostgreSQL server."""
    monkeypatch.setattr("godoo_cli.runtime.restore.database_exists", lambda *_args: False)


@pytest.mark.parametrize("db_name", ["/tmp/escape", "../escape", "nested/runtime", ".."])
def test_runtime_filestore_path_rejects_names_outside_data_directory(tmp_path: Path, db_name: str):
    """Guards the contract that runtime filestore path rejects names outside data directory."""
    with pytest.raises(RuntimeRestoreError, match="Unsafe database name"):
        runtime_filestore_path(tmp_path, db_name)


def test_custom_restore_validates_then_restores_database_and_filestore(tmp_path: Path):
    """Guards the contract that custom restore validates then restores database and filestore."""
    dump = tmp_path / "runtime.dump"
    dump.write_bytes(b"custom dump")
    source = tmp_path / "source-filestore"
    source.mkdir()
    (source / "blob").write_text("data")
    calls: list[list[str]] = []
    created: list[tuple[str, str]] = []
    swapped: list[tuple[str, str]] = []
    cleaned: list[str] = []

    restore_custom_runtime(
        connection=_connection(),
        db_template="template0",
        dump_path=dump,
        filestore_source=source,
        data_dir=tmp_path / "data",
        runner=lambda command: calls.append(list(command)) or 0,
        database_creator=lambda connection, template: created.append((connection.db_name, template)),
        database_swapper=lambda connection, staged: swapped.append((connection.db_name, staged)) or "previous",
        database_cleaner=lambda connection: cleaned.append(connection.db_name),
    )

    assert calls[0] == ["pg_restore", "--format=custom", "--list", str(dump)]
    staged_database = created[0][0]
    assert staged_database.startswith("godoo_restore_")
    assert calls[1][0] == "pg_restore"
    assert set(calls[1][1:-1]) == {
        "--no-owner",
        "--no-privileges",
        "--dbname",
        staged_database,
        "--host",
        "db",
        "--port",
        "5432",
        "--username",
        "odoo",
    }
    assert calls[1][-1] == str(dump)
    assert created == [(staged_database, "template0")]
    assert swapped == [("runtime", staged_database)]
    assert cleaned == ["previous"]
    assert (tmp_path / "data" / "filestore" / "runtime" / "blob").read_text() == "data"


@pytest.mark.parametrize(("dump_exists", "filestore_exists"), [(False, True), (True, False)])
def test_custom_restore_preflight_fails_before_database_replacement(
    tmp_path: Path, dump_exists: bool, filestore_exists: bool
):
    """Guards the contract that custom restore preflight fails before database replacement."""
    dump = tmp_path / "runtime.dump"
    source = tmp_path / "filestore"
    if dump_exists:
        dump.write_bytes(b"dump")
    if filestore_exists:
        source.mkdir()
    replaced: list[bool] = []

    with pytest.raises(RuntimeRestoreError):
        restore_custom_runtime(
            connection=_connection(),
            db_template="template0",
            dump_path=dump,
            filestore_source=source,
            data_dir=tmp_path / "data",
            runner=lambda _command: 0,
            database_creator=lambda *_args: replaced.append(True),
            database_swapper=lambda *_args: pytest.fail("database swap must not run"),
        )
    assert not replaced


def test_filestore_staging_failure_does_not_clean_existing_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that filestore staging failure does not clean existing database."""
    dump = tmp_path / "runtime.dump"
    dump.write_bytes(b"dump")
    source = tmp_path / "source"
    source.mkdir()
    cleaned: list[bool] = []

    def fail_copy(_source: Path, _stage: Path) -> None:
        error = "disk full"
        raise OSError(error)

    monkeypatch.setattr("godoo_cli.runtime.restore.copy_filestore", fail_copy)

    with pytest.raises(OSError, match="disk full"):
        restore_custom_runtime(
            connection=_connection(),
            db_template="template0",
            dump_path=dump,
            filestore_source=source,
            data_dir=tmp_path / "data",
            runner=lambda _command: 0,
            database_creator=lambda *_args: pytest.fail("staging database creation must not run"),
            database_cleaner=lambda _connection: cleaned.append(True),
            database_swapper=lambda *_args: pytest.fail("database swap must not run"),
        )

    assert not cleaned


def test_failed_restore_keeps_existing_filestore(tmp_path: Path):
    """Guards the contract that failed restore keeps existing filestore."""
    dump = tmp_path / "runtime.dump"
    dump.write_bytes(b"dump")
    source = tmp_path / "source"
    source.mkdir()
    (source / "new").write_text("new")
    target = tmp_path / "data" / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "old").write_text("old")
    created: list[str] = []
    cleaned: list[str] = []

    with pytest.raises(RuntimeRestoreError):
        restore_custom_runtime(
            connection=_connection(),
            db_template="template0",
            dump_path=dump,
            filestore_source=source,
            data_dir=tmp_path / "data",
            runner=lambda command: 0 if "--list" in command else 7,
            database_creator=lambda connection, _template: created.append(connection.db_name),
            database_cleaner=lambda connection: cleaned.append(connection.db_name),
            database_swapper=lambda *_args: pytest.fail("incomplete restore must not replace the target database"),
        )
    assert cleaned == created
    assert (target / "old").read_text() == "old"
    assert not (target / "new").exists()


def test_filestore_swap_failure_restores_previous_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that filestore swap failure restores previous database."""
    dump = tmp_path / "runtime.dump"
    dump.write_bytes(b"dump")
    source = tmp_path / "source"
    source.mkdir()
    rollback: list[tuple[str, str | None]] = []

    def fail_filestore_swap(_stage: Path, _target: Path) -> None:
        message = "filestore unavailable"
        raise RuntimeRestoreError(message)

    monkeypatch.setattr("godoo_cli.runtime.promotion.replace_filestore", fail_filestore_swap)

    with pytest.raises(RuntimeRestoreError, match="filestore unavailable"):
        restore_custom_runtime(
            connection=_connection(),
            db_template="template0",
            dump_path=dump,
            filestore_source=source,
            data_dir=tmp_path / "data",
            runner=lambda _command: 0,
            database_creator=lambda *_args: None,
            database_swapper=lambda *_args: "previous",
            database_rollback=lambda connection, backup: rollback.append((connection.db_name, backup)),
        )

    assert rollback == [("runtime", "previous")]


def test_filestore_swap_and_database_rollback_failure_retains_recovery_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Guards pending restore marker and staging files when rollback fails."""
    dump = tmp_path / "runtime.dump"
    dump.write_bytes(b"dump")
    source = tmp_path / "source"
    source.mkdir()
    data_dir = tmp_path / "data"

    def fail_filestore_swap(_stage: Path, _target: Path) -> None:
        message = "filestore unavailable"
        raise RuntimeRestoreError(message)

    def fail_rollback(_connection: DBConnection, _backup: str | None) -> None:
        message = "postgres unavailable during rollback"
        raise ConnectionError(message)

    monkeypatch.setattr("godoo_cli.runtime.promotion.replace_filestore", fail_filestore_swap)
    with pytest.raises(RuntimeRestoreError, match="pending marker was retained"):
        restore_custom_runtime(
            connection=_connection(),
            db_template="template0",
            dump_path=dump,
            filestore_source=source,
            data_dir=data_dir,
            runner=lambda _command: 0,
            database_creator=lambda *_args: None,
            database_swapper=lambda *_args: "previous",
            database_rollback=fail_rollback,
        )

    assert runtime_restore_marker(data_dir, "runtime").exists()
    assert list((data_dir / "filestore").glob(".runtime.restore-*"))


def test_completed_restore_keeps_database_when_old_filestore_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Guards the contract that completed restore keeps database when old filestore cleanup fails."""
    dump = tmp_path / "runtime.dump"
    dump.write_bytes(b"dump")
    source = tmp_path / "source"
    source.mkdir()
    (source / "new").write_text("new")
    target = tmp_path / "data" / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "old").write_text("old")
    cleaned: list[bool] = []

    def fail_backup_cleanup(path: Path) -> None:
        assert path.name.startswith(".runtime.before-restore-")
        message = "backup volume is read-only"
        raise OSError(message)

    monkeypatch.setattr("godoo_cli.runtime.restore.shutil.rmtree", fail_backup_cleanup)

    restore_custom_runtime(
        connection=_connection(),
        db_template="template0",
        dump_path=dump,
        filestore_source=source,
        data_dir=tmp_path / "data",
        force=True,
        runner=lambda _command: 0,
        database_creator=lambda *_args: None,
        database_cleaner=lambda _connection: cleaned.append(True),
        database_swapper=lambda *_args: None,
    )

    assert not cleaned
    assert (target / "new").read_text() == "new"
    assert list(target.parent.glob(".runtime.before-restore-*"))
