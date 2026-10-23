"""Focused ZIP CRC verification behavior for runtime archive restores."""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.commands.runtime import init as runtime_init
from godoo_cli.models import GodooConfig
from godoo_cli.runtime import archive as runtime_archive
from godoo_cli.runtime.lifecycle import LifecycleOutcome


def test_default_validation_does_not_read_archive_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Structural validation does not scan ZIP payloads unless explicitly requested."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
        archive.writestr("unused", "payload")
    original_open = zipfile.ZipFile.open
    reads: list[str] = []

    def count_payload_reads(
        archive: zipfile.ZipFile, name: str | zipfile.ZipInfo, *args: object, **kwargs: object
    ) -> zipfile.ZipExtFile:
        reads.append(getattr(name, "filename", name))
        return original_open(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", count_payload_reads)
    runtime_archive.validate_native_runtime_archive(archive_path)

    assert not reads


def test_opt_in_crc_scan_rejects_corrupt_unused_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The optional scan checks members that restore would not otherwise extract."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
        archive.writestr("unused", "payload")
    original_open = zipfile.ZipFile.open

    def corrupt_unused_member(
        archive: zipfile.ZipFile, name: str | zipfile.ZipInfo, *args: object, **kwargs: object
    ) -> zipfile.ZipExtFile:
        if getattr(name, "filename", name) == "unused":
            message = "corrupt unused member"
            raise zipfile.BadZipFile(message)
        return original_open(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", corrupt_unused_member)

    with pytest.raises(runtime_archive.RuntimeRestoreError, match="corrupt member 'unused'"):
        runtime_archive.validate_native_runtime_archive(archive_path, verify_archive_crc=True)


def test_extracting_dump_still_rejects_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Extraction continues to rely on zipfile's built-in CRC validation."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
    validated = runtime_archive.validate_native_runtime_archive(archive_path)
    assert validated is None
    token = runtime_archive._validate_native_runtime_archive(archive_path)
    original_open = zipfile.ZipFile.open

    def corrupt_dump(
        archive: zipfile.ZipFile, name: str | zipfile.ZipInfo, *args: object, **kwargs: object
    ) -> zipfile.ZipExtFile:
        if getattr(name, "filename", name) == "dump.sql":
            message = "corrupt dump"
            raise zipfile.BadZipFile(message)
        return original_open(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", corrupt_dump)
    with (
        pytest.raises(zipfile.BadZipFile, match="corrupt dump"),
        runtime_archive._open_validated_archive(archive_path, token) as archive,
    ):
        archive.extract("dump.sql", tmp_path / "extracted")


def test_original_filestore_crc_preflight_extracts_each_member_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full scan writes the staged payload instead of rereading it for extraction."""
    archive_path = tmp_path / "runtime.zip"
    original_filestore = tmp_path / "original"
    original_filestore.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
        archive.writestr("filestore/attachment", "payload")
    original_open = zipfile.ZipFile.open
    reads: list[str] = []

    def count_reads(
        archive: zipfile.ZipFile, name: str | zipfile.ZipInfo, *args: object, **kwargs: object
    ) -> zipfile.ZipExtFile:
        reads.append(getattr(name, "filename", name))
        return original_open(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", count_reads)
    validated = runtime_archive.prepare_archive_with_original_filestore(
        archive_path,
        original_filestore,
        verify_archive_crc=True,
    )

    assert validated is not None
    assert reads == ["dump.sql", "filestore/attachment"]
    validated.cleanup()


def test_default_original_filestore_preflight_reports_extraction(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Default preflight names extraction while ZIP reads enforce member CRCs."""
    archive_path = tmp_path / "runtime.zip"
    original_filestore = tmp_path / "original"
    original_filestore.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
        archive.writestr("filestore/attachment", "payload")

    caplog.set_level(logging.INFO, logger=runtime_archive.LOGGER.name)
    validated = runtime_archive.prepare_archive_with_original_filestore(archive_path, original_filestore)

    assert validated is not None
    assert (
        "Validating archive structure and identity; extracting dump and filestore with ZIP CRC checks."
        in caplog.messages
    )
    assert any(message.startswith("Extracted runtime archive:") for message in caplog.messages)
    assert not any("Validating runtime archive CRCs:" in message for message in caplog.messages)
    validated.cleanup()


def test_runtime_init_forwards_crc_option_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime init passes the environment-selected scan mode to archive restore."""
    archive_path = tmp_path / "seed.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dump.sql", "select 1")
    config = GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        data_dir=tmp_path / "data",
        db_name="runtime",
    )
    observed: dict[str, object] = {}
    monkeypatch.setattr(runtime_init, "resolve_command_config", lambda **_kwargs: config)
    monkeypatch.setattr(runtime_init, "require_odoo_version", lambda *_args: SimpleNamespace(major=19, raw="19.0"))
    monkeypatch.setattr(runtime_init, "x_sendfile_enabled", lambda *_args: False)
    monkeypatch.setattr(runtime_init, "prepare_archive_with_original_filestore", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime_init, "load_runtime_archive", lambda **kwargs: observed.update(kwargs) or 0)

    def deployment(current: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        cast(Callable[[GodooConfig], None], kwargs["seeder"])(current)
        return LifecycleOutcome.RESTORED, 0

    monkeypatch.setattr(runtime_init, "deployment_init", deployment)
    app = typer.Typer()
    app.command()(runtime_init.deployment_init_odoo_runtime)
    result = CliRunner().invoke(
        app,
        [],
        env={
            "ODOO_MAIN_FOLDER": str(config.odoo_install_folder),
            "ODOO_WORKSPACE_ADDON_LOCATION": str(config.workspace_addon_path),
            "ODOO_CONF_PATH": str(config.odoo_conf_path),
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
            "GODOO_RUNTIME_SEED": str(archive_path),
            "GODOO_VERIFY_ARCHIVE_CRC": "true",
        },
    )

    assert result.exit_code == 0, result.output
    assert observed["verify_archive_crc"] is True
