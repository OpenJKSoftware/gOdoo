"""Tests for Odoo-native database lifecycle wrappers."""

import zipfile
from pathlib import Path

import pytest

from godoo_cli.commands.db import archive
from godoo_cli.commands.db.archive import (
    RuntimeRestoreError,
    dump_runtime_archive,
    load_legacy_runtime_dump,
    load_runtime_archive,
)
from godoo_cli.commands.db.reset import reset_empty_runtime, reset_runtime_from_template


def test_template_reset_delegates_to_odoo_duplicate_with_data_dir():
    commands = []
    result = reset_runtime_from_template(
        db_name="runtime",
        db_template_name="template",
        odoo_bin_path=Path("/odoo/odoo-bin"),
        odoo_conf_path=Path("/project/odoo.conf"),
        data_dir=Path("/var/lib/odoo"),
        runner=lambda command: commands.append(command) or 17,
    )
    assert result == 17
    assert commands == [
        [
            "/odoo/odoo-bin",
            "db",
            "--config",
            "/project/odoo.conf",
            "--data-dir",
            "/var/lib/odoo",
            "duplicate",
            "--force",
            "template",
            "runtime",
        ]
    ]


def test_empty_reset_delegates_to_odoo_drop_and_preserves_return_code():
    commands = []
    result = reset_empty_runtime(
        db_name="runtime",
        odoo_bin_path=Path("/odoo/odoo-bin"),
        runner=lambda command: commands.append(command) or 9,
    )
    assert result == 9
    assert commands == [["/odoo/odoo-bin", "db", "drop", "runtime"]]


def test_dump_delegates_to_temporary_odoo_zip_archive(tmp_path: Path):
    commands = []
    destination = tmp_path / "template.zip"
    result = dump_runtime_archive(
        db_name="template",
        archive_path=destination,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        odoo_conf_path=Path("/project/odoo.conf"),
        data_dir=Path("/var/lib/odoo"),
        runner=lambda command: commands.append(command) or 7,
    )
    assert result == 7
    command = commands[0]
    assert command[:-1] == [
        "/odoo/odoo-bin",
        "db",
        "--config",
        "/project/odoo.conf",
        "--data-dir",
        "/var/lib/odoo",
        "dump",
        "template",
    ]
    assert Path(command[-1]).parent == tmp_path
    assert Path(command[-1]).name.startswith(".template.zip.")
    assert not destination.exists()


def test_dump_replaces_destination_only_after_success(tmp_path: Path):
    destination = tmp_path / "runtime.zip"
    destination.write_bytes(b"previous")

    def create_archive(command: list[str]) -> int:
        Path(command[-1]).write_bytes(b"new")
        return 0

    assert (
        dump_runtime_archive(
            db_name="runtime",
            archive_path=destination,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            runner=create_archive,
        )
        == 0
    )
    assert destination.read_bytes() == b"new"
    assert list(tmp_path.iterdir()) == [destination]


def test_load_delegates_to_odoo_zip_archive_and_preserves_return_code(tmp_path: Path):
    commands = []
    archive_path = tmp_path / "template.zip"
    with zipfile.ZipFile(archive_path, "w") as runtime_zip:
        runtime_zip.writestr("dump.sql", "select 1;")
    result = load_runtime_archive(
        db_name="template",
        archive_path=archive_path,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        force=True,
        runner=lambda command: commands.append(command) or 13,
    )
    assert result == 13
    assert commands == [
        [
            "/odoo/odoo-bin",
            "db",
            "load",
            "--force",
            "template",
            str(archive_path),
        ]
    ]


def test_invalid_archive_fails_before_forced_load(tmp_path: Path):
    archive_path = tmp_path / "invalid.zip"
    archive_path.write_bytes(b"not a zip")
    calls: list[list[str]] = []

    with pytest.raises(RuntimeRestoreError, match="not a valid ZIP"):
        load_runtime_archive(
            db_name="runtime",
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            force=True,
            runner=lambda command: calls.append(list(command)) or 0,
        )

    assert calls == []


def test_load_database_reports_invalid_native_archive_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    archive_path = tmp_path / "invalid.zip"
    archive_path.write_bytes(b"not a zip")
    monkeypatch.setattr(archive, "require_odoo_version", lambda *_args: None)
    monkeypatch.setattr(archive.CLI, "returner", lambda code: code)

    result = archive.load_database(
        db_name="runtime",
        archive_path=archive_path,
        odoo_main_path=Path("/odoo"),
        force=True,
    )

    assert result == 1


def test_load_legacy_dump_selects_the_single_legacy_filestore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "legacy"
    dump = source / "odoo.dump"
    filestore = source / "odoo_filestore" / "filestore" / "previous-runtime"
    filestore.mkdir(parents=True)
    dump.write_bytes(b"custom dump")
    observed: dict[str, object] = {}

    monkeypatch.setattr(archive, "restore_custom_runtime", lambda **kwargs: observed.update(kwargs))

    load_legacy_runtime_dump(
        db_name="runtime",
        source_folder=source,
        data_dir=tmp_path / "data",
        db_host="db",
        db_port=5432,
        db_user="odoo",
        db_password="secret",
        db_template="template0",
    )

    assert observed["dump_path"] == dump
    assert observed["filestore_source"] == filestore


def test_load_legacy_dump_rejects_ambiguous_filestores(tmp_path: Path):
    source = tmp_path / "legacy"
    (source / "odoo_filestore" / "filestore" / "first").mkdir(parents=True)
    (source / "odoo_filestore" / "filestore" / "second").mkdir()

    with pytest.raises(RuntimeRestoreError, match="unambiguous filestore"):
        load_legacy_runtime_dump(
            db_name="runtime",
            source_folder=source,
            data_dir=tmp_path / "data",
            db_host="",
            db_port=0,
            db_user="odoo",
            db_password="",
            db_template="template0",
        )


def test_load_legacy_directory_requires_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(archive, "require_odoo_version", lambda *_args: None)
    monkeypatch.setattr(archive.CLI, "returner", lambda code: code)

    result = archive.load_database(
        db_name="runtime",
        archive_path=tmp_path,
        odoo_main_path=Path("/odoo"),
        force=False,
    )

    assert result == 2


def test_load_legacy_directory_delegates_when_forced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    observed: dict[str, object] = {}
    monkeypatch.setattr(archive, "require_odoo_version", lambda *_args: None)
    monkeypatch.setattr(archive.CLI, "returner", lambda code: code)
    monkeypatch.setattr(archive, "load_legacy_runtime_dump", lambda **kwargs: observed.update(kwargs))

    result = archive.load_database(
        db_name="runtime",
        archive_path=tmp_path,
        odoo_main_path=Path("/odoo"),
        data_dir=Path("/data"),
        db_host="db",
        db_port=5432,
        db_user="odoo",
        db_password="secret",
        force=True,
    )

    assert result == 0
    assert observed["source_folder"] == tmp_path
    assert observed["db_template"] == "template0"
