"""Tests for database preparation strategy selection and recovery markers."""

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from godoo_cli.commands.db.cli import db_cli_app
from godoo_cli.runtime.archive import (
    RuntimeRestoreError,
    _reuse_or_validate_archive,
    _validate_native_runtime_archive,
)
from godoo_cli.runtime.locks import read_runtime_lifecycle, runtime_readiness_marker
from godoo_cli.runtime.prepare import (
    PrepareStrategy,
    _bootstrap_database_template,
    prepare_runtime,
    prepare_runtime_pair,
    select_prepare_strategy,
)


@pytest.mark.parametrize("odoo_major", [16, 17, 18])
def test_bootstrap_template_ignores_environment_before_odoo_19(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    odoo_major: int,
) -> None:
    config_path = tmp_path / "odoo.conf"
    config_path.write_text("[options]\ndb_template = configured_template\n", encoding="utf-8")
    config = SimpleNamespace(odoo_conf_path=config_path)
    monkeypatch.setenv("PGDATABASE_TEMPLATE", "unsupported_environment_template")

    assert _bootstrap_database_template(config, odoo_major) == "configured_template"


def test_bootstrap_template_prefers_environment_in_odoo_19(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "odoo.conf"
    config_path.write_text("[options]\ndb_template = configured_template\n", encoding="utf-8")
    config = SimpleNamespace(odoo_conf_path=config_path)
    monkeypatch.setenv("PGDATABASE_TEMPLATE", "environment_template")

    assert _bootstrap_database_template(config, 19) == "environment_template"


def test_bootstrap_template_observes_config_changes_before_odoo_19(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "odoo.conf"
    config_path.write_text("[options]\ndb_template = first_template\n", encoding="utf-8")
    config = SimpleNamespace(odoo_conf_path=config_path)
    monkeypatch.setenv("PGDATABASE_TEMPLATE", "ignored_environment_template")

    first_template = _bootstrap_database_template(config, 16)
    config_path.write_text("[options]\ndb_template = second_template\n", encoding="utf-8")

    assert _bootstrap_database_template(config, 16) != first_template


def test_auto_strategy_uses_documented_order() -> None:
    """Guards the contract that auto strategy uses documented order."""
    assert select_prepare_strategy(PrepareStrategy.AUTO, cow_available=True).strategy == PrepareStrategy.COW
    assert select_prepare_strategy(PrepareStrategy.AUTO, postgres_archive=True).strategy == PrepareStrategy.POSTGRES
    assert select_prepare_strategy(PrepareStrategy.AUTO, odoo_archive=True).strategy == PrepareStrategy.ODOO
    assert select_prepare_strategy(PrepareStrategy.AUTO).strategy == PrepareStrategy.BOOTSTRAP


def test_prepare_rejects_unknown_strategy_without_traceback() -> None:
    """Guards the contract that prepare rejects unknown strategy without traceback."""
    result = CliRunner().invoke(
        db_cli_app(),
        ["prepare", "--strategy", "nonsense"],
        env={
            "ODOO_MAIN_DB": "runtime",
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
        },
    )

    assert result.exit_code == 2
    assert "Unknown database preparation strategy: nonsense" in result.output
    assert "Traceback" not in result.output


def _native_archive(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("dump.sql", "-- seed")
    return path


def test_lifecycle_archive_identity_rejects_replaced_file(tmp_path: Path) -> None:
    archive = _native_archive(tmp_path / "seed.zip")
    with pytest.raises(RuntimeRestoreError, match="identity was not captured"):
        _reuse_or_validate_archive(archive, None, require_same_identity=True)
    validated = _validate_native_runtime_archive(archive)
    with zipfile.ZipFile(archive, "w") as replacement:
        replacement.writestr("dump.sql", "-- replacement seed")

    with pytest.raises(RuntimeRestoreError, match="changed after lifecycle plan"):
        _reuse_or_validate_archive(archive, validated, require_same_identity=True)

    refreshed = _reuse_or_validate_archive(archive, validated)
    assert refreshed.identity != validated.identity


@pytest.mark.parametrize("as_environment", [False, True])
def test_original_filestore_auto_selects_native_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_environment: bool
) -> None:
    """An original filestore keeps automatic preparation on the native ZIP path."""
    source = tmp_path / "original-filestore"
    source.mkdir()
    archive = _native_archive(tmp_path / "seed.zip")
    observed: dict[str, object] = {}
    original_testzip = zipfile.ZipFile.testzip
    crc_checks = 0

    def count_crc_checks(native_archive: zipfile.ZipFile) -> str | None:
        nonlocal crc_checks
        crc_checks += 1
        return original_testzip(native_archive)

    monkeypatch.setattr(zipfile.ZipFile, "testzip", count_crc_checks)
    monkeypatch.setattr(
        "godoo_cli.runtime.prepare.require_supported_odoo_runtime",
        lambda *_args: SimpleNamespace(major=19),
    )
    monkeypatch.setattr(
        "godoo_cli.runtime.prepare._cow_capability_available",
        lambda *_args: pytest.fail("CoW must not be probed for an original filestore"),
    )
    monkeypatch.setattr(
        "godoo_cli.runtime.prepare.load_runtime_archive",
        lambda **kwargs: observed.update(kwargs) or 0,
    )
    original_option = [] if as_environment else ["--original-filestore", str(source)]
    env = {
        "ODOO_MAIN_DB": "target",
        "ODOO_MAIN_FOLDER": str(tmp_path / "odoo"),
        "ODOO_CONF_PATH": str(tmp_path / "odoo.conf"),
        "ODOO_WORKSPACE_ADDON_LOCATION": str(tmp_path / "addons"),
        "ODOO_DATA_DIR": str(tmp_path / "data"),
    }
    if as_environment:
        env["GODOO_ORIGINAL_FILESTORE"] = str(source)

    result = CliRunner().invoke(
        db_cli_app(),
        ["prepare", "--archive", str(archive), "--source-db", "source", *original_option],
        env=env,
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "odoo"
    assert observed["archive_path"] == archive
    assert observed["original_filestore"] == source
    assert observed["_validated_archive"] is not None
    assert crc_checks == 1


@pytest.mark.parametrize(
    ("strategy", "archive_suffix", "archive_is_directory", "error_text"),
    [
        ("cow", ".zip", False, "requires the Odoo preparation strategy"),
        ("postgres", ".zip", False, "requires the Odoo preparation strategy"),
        ("bootstrap", ".zip", False, "requires the Odoo preparation strategy"),
        ("auto", ".dump", False, "requires an Odoo ZIP archive"),
        ("auto", ".zip", True, "legacy archive directory"),
    ],
)
def test_original_filestore_rejects_incompatible_prepare_inputs(
    tmp_path: Path,
    strategy: str,
    archive_suffix: str,
    archive_is_directory: bool,
    error_text: str,
) -> None:
    """Incompatible original-filestore inputs fail before preparation begins."""
    source = tmp_path / "original-filestore"
    source.mkdir()
    archive = tmp_path / f"seed{archive_suffix}"
    if archive_is_directory:
        archive.mkdir()
    elif archive_suffix == ".zip":
        _native_archive(archive)
    else:
        archive.write_bytes(b"postgres dump")
    config = SimpleNamespace(
        db_name="target",
        data_dir=tmp_path / "data",
        db_connection=object(),
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=None,
    )

    with pytest.raises(ValueError, match=error_text):
        prepare_runtime(
            config,
            strategy=strategy,
            archive_path=archive,
            original_filestore=source,
        )
    assert not runtime_readiness_marker(config.data_dir, config.db_name).exists()


def test_prepare_keeps_marker_when_callback_returns_failure(tmp_path: Path) -> None:
    """Guards the contract that prepare keeps marker when callback returns failure."""
    with pytest.raises(RuntimeError, match="exit code 7"):
        prepare_runtime_pair(
            data_dir=tmp_path,
            db_name="runtime",
            strategy=PrepareStrategy.BOOTSTRAP,
            bootstrap=lambda: 7,
        )
    assert runtime_readiness_marker(tmp_path, "runtime").exists()


def test_bootstrap_retry_rejects_changed_database_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "odoo.conf"
    config_path.write_text("[options]\ndb_template = template_file\n", encoding="utf-8")
    config = SimpleNamespace(
        db_name="runtime",
        data_dir=tmp_path / "data",
        db_connection=SimpleNamespace(hostname="db", port=5432, username="odoo", sslmode=None),
        db_host="db",
        db_port=5432,
        db_user="odoo",
        db_password="",
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=config_path,
    )
    monkeypatch.setattr(
        "godoo_cli.runtime.prepare._cow_capability_available",
        lambda _config, _source_db: False,
    )
    monkeypatch.setattr(
        "godoo_cli.runtime.prepare.require_supported_odoo_runtime",
        lambda _path: SimpleNamespace(major=19),
    )
    monkeypatch.setenv("PGDATABASE_TEMPLATE", "template_env_a")
    monkeypatch.setattr("godoo_cli.runtime.prepare.ensure_runtime", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="Bootstrap preparation did not create"):
        prepare_runtime(config, strategy="bootstrap")

    marker = runtime_readiness_marker(config.data_dir, config.db_name)
    state = read_runtime_lifecycle(marker, config.db_name)
    assert state is not None
    assert state["preparation"]["bootstrap_db_template"] == "template_env_a"

    calls: list[bool] = []
    monkeypatch.setenv("PGDATABASE_TEMPLATE", "template_env_b")
    monkeypatch.setattr(
        "godoo_cli.runtime.prepare.ensure_runtime",
        lambda *_args, **_kwargs: calls.append(True) or True,
    )
    with pytest.raises(RuntimeError, match="preparation inputs differ"):
        prepare_runtime(config, strategy="bootstrap")
    assert calls == []


def test_prepare_clears_marker_after_successful_completion(tmp_path: Path) -> None:
    """Guards the contract that prepare clears marker after successful completion."""
    prepare_runtime_pair(
        data_dir=tmp_path,
        db_name="runtime",
        strategy=PrepareStrategy.BOOTSTRAP,
        bootstrap=lambda: True,
        completion=lambda: 0,
    )
    assert not runtime_readiness_marker(tmp_path, "runtime").exists()
