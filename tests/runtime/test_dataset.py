"""Tests for the fixed runtime dataset resolver."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import godoo_cli.runtime.dataset as dataset_module
from godoo_cli.commands.runtime import init as runtime_init
from godoo_cli.runtime.restore import RuntimeRestoreError


def _dataset_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "dataset"
    filestore = root / "odoo_filestore" / "filestore" / "odoo"
    filestore.mkdir(parents=True)
    (root / "odoo.dump").write_bytes(b"custom dump")
    (root / "upgraded.zip").write_bytes(b"zip")
    return root, filestore


@pytest.fixture(autouse=True)
def fake_validators(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dataset_module, "validate_custom_dump", lambda _path: None)
    monkeypatch.setattr(dataset_module, "custom_dump_identity", lambda _path: (1, 2, 3, 4, 5))


def test_plain_dataset_selects_source_database_filestore(tmp_path: Path) -> None:
    root, filestore = _dataset_root(tmp_path)

    resolved = dataset_module.resolve_runtime_dataset(
        dataset_root=root,
        seed=None,
        original_filestore=None,
    )

    assert resolved is not None
    assert resolved.kind == "custom"
    assert resolved.source == root / "odoo.dump"
    assert resolved.original_filestore == filestore
    assert resolved.dump_identity == (1, 2, 3, 4, 5)


def test_plain_dataset_honors_explicit_original_filestore(tmp_path: Path) -> None:
    root, _filestore = _dataset_root(tmp_path)
    explicit = tmp_path / "explicit-original"
    explicit.mkdir()

    resolved = dataset_module.resolve_runtime_dataset(
        dataset_root=root,
        seed=None,
        original_filestore=explicit,
    )

    assert resolved is not None
    assert resolved.original_filestore == explicit


def test_plain_dataset_does_not_implicitly_select_paired_upgrade(tmp_path: Path) -> None:
    root, filestore = _dataset_root(tmp_path)

    resolved = dataset_module.resolve_runtime_dataset(
        dataset_root=root,
        seed=None,
        original_filestore=None,
    )

    assert resolved is not None
    assert resolved.kind == "custom"
    assert resolved.source == root / "odoo.dump"
    assert resolved.original_filestore == filestore


def test_explicit_seed_wins_and_explicit_original_overrides_inference(tmp_path: Path) -> None:
    root, _filestore = _dataset_root(tmp_path)
    explicit_seed = tmp_path / "chosen.zip"
    explicit_seed.write_bytes(b"zip")
    explicit_original = tmp_path / "original"
    explicit_original.mkdir()

    resolved = dataset_module.resolve_runtime_dataset(
        dataset_root=root,
        seed=explicit_seed,
        original_filestore=explicit_original,
    )

    assert resolved is not None
    assert resolved.source == explicit_seed
    assert resolved.original_filestore == explicit_original


def test_explicit_seed_ignores_missing_unused_dataset_root(tmp_path: Path) -> None:
    seed = tmp_path / "chosen.zip"
    seed.write_bytes(b"zip")

    resolved = dataset_module.resolve_runtime_dataset(
        dataset_root=tmp_path / "missing-dataset",
        seed=seed,
        original_filestore=None,
    )

    assert resolved is not None
    assert resolved.source == seed


def test_equivalent_paired_seed_path_infers_original_filestore(tmp_path: Path) -> None:
    root, filestore = _dataset_root(tmp_path)
    paired_seed = root / ".." / root.name / "upgraded.zip"

    resolved = dataset_module.resolve_runtime_dataset(
        dataset_root=root,
        seed=paired_seed,
        original_filestore=None,
    )

    assert resolved is not None
    assert resolved.original_filestore == filestore


def test_missing_plain_filestore_fails_before_runtime_work(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "odoo.dump").write_bytes(b"custom dump")

    with pytest.raises(RuntimeRestoreError, match="Filestore directory does not exist"):
        dataset_module.resolve_runtime_dataset(
            dataset_root=root,
            seed=None,
            original_filestore=None,
        )


def test_explicit_paired_upgrade_defers_integrity_validation_to_init(tmp_path: Path) -> None:
    root, _filestore = _dataset_root(tmp_path)

    resolved = dataset_module.resolve_runtime_dataset(
        dataset_root=root,
        seed=root / "upgraded.zip",
        original_filestore=None,
    )

    assert resolved is not None
    assert resolved.source == root / "upgraded.zip"


def test_dump_replaced_during_validation_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _filestore = _dataset_root(tmp_path)
    identities = iter([(1, 2, 3, 4, 5), (1, 2, 4, 4, 5)])
    monkeypatch.setattr(dataset_module, "custom_dump_identity", lambda _path: next(identities))

    with pytest.raises(RuntimeRestoreError, match="changed during validation"):
        dataset_module.resolve_runtime_dataset(
            dataset_root=root,
            seed=None,
            original_filestore=None,
        )


def test_demo_seed_conflict_rejects_before_dataset_or_runtime_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = typer.Typer()
    app.command()(runtime_init.deployment_init_odoo_runtime)
    monkeypatch.setattr(runtime_init, "resolve_runtime_dataset", lambda **_kwargs: pytest.fail("resolver must not run"))
    monkeypatch.setattr(runtime_init, "discover_hooks", lambda *_args: pytest.fail("hooks must not run"))
    monkeypatch.setattr(runtime_init, "resolve_command_config", lambda **_kwargs: pytest.fail("config must not run"))
    monkeypatch.setattr(runtime_init, "deployment_init", lambda *_args, **_kwargs: pytest.fail("init must not run"))

    result = CliRunner().invoke(
        app,
        ["--seed", str(tmp_path / "seed.zip"), "--odoo-demo"],
        env={
            "ODOO_MAIN_FOLDER": str(tmp_path / "odoo"),
            "ODOO_WORKSPACE_ADDON_LOCATION": str(tmp_path / "addons"),
            "ODOO_CONF_PATH": str(tmp_path / "odoo.conf"),
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )

    assert result.exit_code == 2, result.output
    assert "--seed cannot be combined with demo data" in result.output
