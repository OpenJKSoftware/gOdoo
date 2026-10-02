"""Tests staged database version compatibility before promotion."""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from godoo_cli.database.connection import DBConnection
from godoo_cli.runtime.archive import RuntimeRestoreError, _validate_staged_base_version, load_runtime_archive


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
            db_name="runtime",
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            connection=DBConnection("host", 5432, "user", "pass", "runtime"),
            data_dir=data_dir,
            force=True,
            odoo_version=19,
            use_native_db_load=True,
            runner=lambda _command: 0,
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
            db_name="runtime",
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=data_dir,
            connection=DBConnection("host", 5432, "user", "pass", "runtime"),
            force=True,
            odoo_version=19,
            use_native_db_load=False,
            runner=lambda _command: 0,
            database_creator=lambda *_args: None,
            database_swapper=lambda *_args: swaps.append(True),
            database_cleaner=lambda connection: cleanups.append(connection.db_name),
        )

    assert not swaps
    assert cleanups
    assert (target / "active").read_text() == "keep"
    assert not (target / "new").exists()
