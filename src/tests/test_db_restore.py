"""Tests for guarded custom PostgreSQL runtime restoration."""

from pathlib import Path
from typing import Optional

import pytest

from godoo_cli.commands.db.restore import RuntimeRestoreError, restore_custom_runtime, runtime_filestore_path
from godoo_cli.models import DBConnection


def _connection() -> DBConnection:
    return DBConnection("db", 5432, "odoo", "secret", "runtime")


@pytest.mark.parametrize("db_name", ["/tmp/escape", "../escape", "nested/runtime", ".."])
def test_runtime_filestore_path_rejects_names_outside_data_directory(tmp_path: Path, db_name: str):
    with pytest.raises(RuntimeRestoreError, match="Unsafe database name"):
        runtime_filestore_path(tmp_path, db_name)


def test_custom_restore_validates_then_restores_database_and_filestore(tmp_path: Path):
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
    assert calls[1] == [
        "pg_restore",
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
        str(dump),
    ]
    assert created == [(staged_database, "template0")]
    assert swapped == [("runtime", staged_database)]
    assert cleaned == ["previous"]
    assert (tmp_path / "data" / "filestore" / "runtime" / "blob").read_text() == "data"


@pytest.mark.parametrize(("dump_exists", "filestore_exists"), [(False, True), (True, False)])
def test_custom_restore_preflight_fails_before_database_replacement(
    tmp_path: Path, dump_exists: bool, filestore_exists: bool
):
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
    dump = tmp_path / "runtime.dump"
    dump.write_bytes(b"dump")
    source = tmp_path / "source"
    source.mkdir()
    cleaned: list[bool] = []

    def fail_copytree(_source: Path, _stage: Path) -> None:
        error = "disk full"
        raise OSError(error)

    monkeypatch.setattr("godoo_cli.commands.db.restore.shutil.copytree", fail_copytree)

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
    dump = tmp_path / "runtime.dump"
    dump.write_bytes(b"dump")
    source = tmp_path / "source"
    source.mkdir()
    rollback: list[tuple[str, Optional[str]]] = []

    def fail_filestore_swap(_stage: Path, _target: Path) -> None:
        message = "filestore unavailable"
        raise RuntimeRestoreError(message)

    monkeypatch.setattr("godoo_cli.commands.db.restore._replace_filestore", fail_filestore_swap)

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


def test_completed_restore_keeps_database_when_old_filestore_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
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

    monkeypatch.setattr("godoo_cli.commands.db.restore.shutil.rmtree", fail_backup_cleanup)

    restore_custom_runtime(
        connection=_connection(),
        db_template="template0",
        dump_path=dump,
        filestore_source=source,
        data_dir=tmp_path / "data",
        runner=lambda _command: 0,
        database_creator=lambda *_args: None,
        database_cleaner=lambda _connection: cleaned.append(True),
        database_swapper=lambda *_args: None,
    )

    assert not cleaned
    assert (target / "new").read_text() == "new"
    assert list(target.parent.glob(".runtime.before-restore-*"))
