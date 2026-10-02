"""Tests for disposable filestore staging and archive overlays."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from godoo_cli.database.connection import DBConnection
from godoo_cli.runtime.archive import (
    RuntimeRestoreError,
    _overlay_filestore,
    _reuse_or_validate_archive,
    _validate_native_runtime_archive,
    _validate_staged_base_version,
    load_runtime_archive,
    validate_native_runtime_archive,
)
from godoo_cli.runtime.filestore import _copy_command, copy_filestore
from godoo_cli.runtime.prepare import validate_original_filestore_source


def test_copy_command_selects_platform_specific_fast_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "stage"

    source.mkdir()
    (source / "attachment").write_text("data")
    assert _copy_command(source, destination, "darwin") == ["cp", "-cRLp", str(source / "attachment"), str(destination)]
    assert _copy_command(source, destination, "linux") == [
        "cp",
        "-RLp",
        "--reflink=auto",
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
    monkeypatch.setattr("godoo_cli.runtime.filestore.subprocess.run", fail_copy)

    copy_filestore(source, destination, platform_name="linux")

    assert calls == 1
    assert sorted(path.name for path in destination.iterdir()) == ["kept"]


def test_archive_overlay_retains_original_and_zip_wins_collisions(tmp_path: Path) -> None:
    destination = tmp_path / "stage"
    destination.mkdir()
    (destination / "retained").write_text("original")
    (destination / "collision").write_text("original")
    archive_path = tmp_path / "upgraded.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
        archive.writestr("filestore/collision", "ZIP version")
        archive.writestr("filestore/new/nested", "new attachment")

    with zipfile.ZipFile(archive_path) as archive:
        _overlay_filestore(archive, destination)

    assert (destination / "retained").read_text() == "original"
    assert (destination / "collision").read_text() == "ZIP version"
    assert (destination / "new/nested").read_text() == "new attachment"


def test_archive_rejects_traversing_filestore_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
        archive.writestr("filestore/../../outside", "bad")

    with pytest.raises(RuntimeRestoreError, match="unsafe filestore path"):
        validate_native_runtime_archive(archive_path)


def test_original_filestore_restore_uses_staged_sql_and_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    source = tmp_path / "original"
    source.mkdir()
    (source / "retained").write_text("old")
    (source / "collision").write_text("old")
    archive_path = tmp_path / "upgraded.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
        archive.writestr("filestore/collision", "new")
        archive.writestr("filestore/addition", "added")
    original_testzip = zipfile.ZipFile.testzip
    crc_checks = 0

    def count_crc_checks(archive: zipfile.ZipFile) -> str | None:
        nonlocal crc_checks
        crc_checks += 1
        return original_testzip(archive)

    monkeypatch.setattr(zipfile.ZipFile, "testzip", count_crc_checks)

    monkeypatch.setattr("godoo_cli.runtime.archive.database_exists", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("godoo_cli.runtime.archive._validate_staged_base_version", lambda *_args: None)
    commands: list[list[str]] = []
    connection = DBConnection("host", 5432, "user", "pass", "runtime")
    validated_archive = validate_original_filestore_source(archive_path, source)
    result = load_runtime_archive(
        db_name="runtime",
        archive_path=archive_path,
        original_filestore=source,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        data_dir=data_dir,
        connection=connection,
        force=True,
        odoo_version=19,
        use_native_db_load=True,
        _validated_archive=validated_archive,
        runner=lambda command: commands.append(list(command)) or 0,
        database_creator=lambda *_args: None,
        database_swapper=lambda *_args: None,
        database_rollback=lambda *_args: None,
        database_cleaner=lambda *_args: None,
    )

    target = data_dir / "filestore" / "runtime"
    assert result == 0
    assert crc_checks == 1
    assert commands[0][0] == "psql"
    assert (target / "retained").read_text() == "old"
    assert (target / "collision").read_text() == "new"
    assert (target / "addition").read_text() == "added"
    (target / "retained").write_text("changed")
    assert (source / "retained").read_text() == "old"


def test_failed_staged_sql_restore_preserves_active_filestore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr("godoo_cli.runtime.archive.database_exists", lambda *_args, **_kwargs: False)
    result = load_runtime_archive(
        db_name="runtime",
        archive_path=archive_path,
        original_filestore=source,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        data_dir=data_dir,
        connection=DBConnection("host", 5432, "user", "pass", "runtime"),
        force=True,
        odoo_version=19,
        use_native_db_load=True,
        runner=lambda _command: 1,
        database_creator=lambda *_args: None,
        database_cleaner=lambda *_args: None,
    )

    assert result == 1
    assert sorted(path.name for path in target.iterdir()) == ["active"]
    assert (target / "active").read_text() == "keep"
    assert (source / "retained").read_text() == "old"
    assert not list((data_dir / "filestore").glob("godoo_native_restore_*"))


def test_replaced_archive_is_validated_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
    original_testzip = zipfile.ZipFile.testzip
    crc_checks = 0

    def count_crc_checks(archive: zipfile.ZipFile) -> str | None:
        nonlocal crc_checks
        crc_checks += 1
        return original_testzip(archive)

    monkeypatch.setattr(zipfile.ZipFile, "testzip", count_crc_checks)
    validated = _validate_native_runtime_archive(archive_path)
    replacement_path = tmp_path / "replacement.zip"
    with zipfile.ZipFile(replacement_path, "w") as archive:
        archive.writestr("dump.sql", "select 2")
    replacement_path.replace(archive_path)

    refreshed = _reuse_or_validate_archive(archive_path, validated)

    assert refreshed.identity != validated.identity
    assert crc_checks == 2


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


def test_archive_rejects_corrupt_member_crc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive_path = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
    monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda _archive: "dump.sql")

    with pytest.raises(RuntimeRestoreError, match=r"corrupt member 'dump\.sql'"):
        validate_native_runtime_archive(archive_path)
