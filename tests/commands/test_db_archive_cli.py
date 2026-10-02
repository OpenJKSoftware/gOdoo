"""Tests the database restore command's original-filestore option."""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from godoo_cli.commands.db import archive as archive_commands
from godoo_cli.commands.db.cli import db_cli_app


def test_restore_reads_original_filestore_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The restore command passes its environment-selected filestore to the loader."""
    original_filestore = tmp_path / "original"
    original_filestore.mkdir()
    archive_path = tmp_path / "seed.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "-- seed")
    original_testzip = zipfile.ZipFile.testzip
    crc_checks = 0

    def count_crc_checks(archive: zipfile.ZipFile) -> str | None:
        nonlocal crc_checks
        crc_checks += 1
        return original_testzip(archive)

    monkeypatch.setattr(zipfile.ZipFile, "testzip", count_crc_checks)
    connection = SimpleNamespace(db_name="target")
    connection.with_overrides = lambda **_kwargs: connection
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        archive_commands.DBConnection,
        "from_odoo_config",
        lambda *_args, **_kwargs: connection,
    )
    monkeypatch.setattr(
        archive_commands,
        "resolve_development_odoo_main_path",
        lambda path: path,
    )
    monkeypatch.setattr(
        archive_commands,
        "require_cli_odoo_version",
        lambda *_args: SimpleNamespace(major=19),
    )
    monkeypatch.setattr(
        archive_commands,
        "load_runtime_archive",
        lambda **kwargs: observed.update(kwargs) or 0,
    )
    env = {
        "ODOO_MAIN_DB": "target",
        "ODOO_MAIN_FOLDER": str(tmp_path / "odoo"),
        "ODOO_CONF_PATH": str(tmp_path / "odoo.conf"),
        "GODOO_ORIGINAL_FILESTORE": str(original_filestore),
    }

    result = CliRunner().invoke(db_cli_app(), ["restore", str(archive_path)], env=env)

    assert result.exit_code == 0, result.output
    assert observed["archive_path"] == archive_path
    assert observed["original_filestore"] == original_filestore
    assert observed["_validated_archive"] is not None
    assert crc_checks == 1


def test_restore_rejects_legacy_archive_directory_with_original_filestore(
    tmp_path: Path,
) -> None:
    """The original filestore option cannot be ignored by legacy archive restore."""
    archive_path = tmp_path / "legacy"
    archive_path.mkdir()
    original_filestore = tmp_path / "original"
    original_filestore.mkdir()
    env = {
        "ODOO_MAIN_DB": "target",
        "ODOO_MAIN_FOLDER": str(tmp_path / "odoo"),
        "ODOO_CONF_PATH": str(tmp_path / "odoo.conf"),
    }

    result = CliRunner().invoke(
        db_cli_app(),
        [
            "restore",
            str(archive_path),
            "--force",
            "--original-filestore",
            str(original_filestore),
        ],
        env=env,
    )

    assert result.exit_code == 2
    assert "legacy archive directory" in result.output
