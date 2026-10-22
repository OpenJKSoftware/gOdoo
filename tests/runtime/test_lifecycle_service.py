"""Focused proofs for explicit runtime initialization."""

import json
from pathlib import Path

import pytest

from godoo_cli.database.state import DbBootstrapStatus
from godoo_cli.models import GodooConfig
from godoo_cli.runtime import lifecycle as runtime_lifecycle
from godoo_cli.runtime.lifecycle import LifecycleOutcome, deployment_init
from godoo_cli.runtime.locks import runtime_readiness_marker


@pytest.fixture(autouse=True)
def supported_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_lifecycle, "require_runtime_database_major", lambda _config: 19)


def _config(tmp_path: Path) -> GodooConfig:
    return GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        odoo_conf_path=tmp_path / "odoo.conf",
        db_name="runtime",
        db_user="odoo",
        data_dir=tmp_path / "data",
    )


def test_init_retries_restore_hooks_after_final_hook_failure(tmp_path: Path) -> None:
    config = _config(tmp_path)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "10_policy.py").write_text("pass\n")
    status = DbBootstrapStatus.NO_DB
    calls: list[str] = []
    attempts = 0

    def seed(_config: GodooConfig) -> None:
        nonlocal status
        calls.append("seed")
        status = DbBootstrapStatus.BOOTSTRAPPED

    def hook(_config: GodooConfig, _script: Path) -> int:
        nonlocal attempts
        attempts += 1
        calls.append(f"hook-{attempts}")
        return 7 if attempts == 1 else 0

    actions = {
        "ensure": lambda _config: False,
        "seeder": seed,
        "reconciler": lambda _config: calls.append("reconcile") or 0,
        "status_getter": lambda _connection: status,
        "hook_runner": hook,
    }
    selected = {"after_restore_dirs": [hooks]}
    assert deployment_init(config, **actions, seed_requested=True, **selected) == (
        LifecycleOutcome.RESTORED,
        7,
    )
    assert runtime_readiness_marker(config.data_dir, config.db_name).exists()
    assert deployment_init(config, **actions, seed_requested=False, **selected) == (LifecycleOutcome.READY, 0)
    assert calls == ["seed", "hook-1", "hook-2", "reconcile"]
    assert not runtime_readiness_marker(config.data_dir, config.db_name).exists()


def test_init_marks_before_bootstrap_and_clears_after_reconcile(tmp_path: Path) -> None:
    config = _config(tmp_path)
    marker = runtime_readiness_marker(config.data_dir, config.db_name)
    status = DbBootstrapStatus.NO_DB

    def ensure(_config: GodooConfig) -> bool:
        nonlocal status
        assert marker.exists()
        assert json.loads(marker.read_text())["outcome"] == "bootstrapped"
        status = DbBootstrapStatus.BOOTSTRAPPED
        return True

    result = deployment_init(
        config,
        ensure=ensure,
        reconciler=lambda _config: 0,
        status_getter=lambda _connection: status,
    )
    assert result == (LifecycleOutcome.BOOTSTRAPPED, 0)
    assert not marker.exists()


def test_major_version_failure_prevents_marker_and_callbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        runtime_lifecycle,
        "require_runtime_database_major",
        lambda _config: (_ for _ in ()).throw(ValueError("unsupported major")),
    )
    with pytest.raises(ValueError, match="unsupported major"):
        deployment_init(
            config,
            ensure=lambda _config: calls.append("bootstrap") or True,
            reconciler=lambda _config: calls.append("reconcile") or 0,
        )
    assert calls == []
    assert not runtime_readiness_marker(config.data_dir, config.db_name).exists()


def test_bootstrap_hook_failure_retries_bootstrap_hooks_after_database_exists(tmp_path: Path) -> None:
    config = _config(tmp_path)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "10_bootstrap.py").write_text("pass\n")
    status = DbBootstrapStatus.NO_DB
    attempts = 0

    def ensure(_config: GodooConfig) -> bool:
        nonlocal status
        status = DbBootstrapStatus.BOOTSTRAPPED
        return True

    def hook(_config: GodooConfig, _script: Path) -> int:
        nonlocal attempts
        attempts += 1
        return 5 if attempts == 1 else 0

    actions = {
        "ensure": ensure,
        "reconciler": lambda _config: 0,
        "status_getter": lambda _connection: status,
        "hook_runner": hook,
    }
    selected = {"after_bootstrap_dirs": [hooks]}
    assert deployment_init(config, **actions, **selected) == (LifecycleOutcome.BOOTSTRAPPED, 5)
    assert deployment_init(config, **actions, **selected) == (LifecycleOutcome.READY, 0)
    assert attempts == 2


def test_init_runs_preflight_before_preparer_and_preserves_state_on_failure(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls: list[str] = []
    actions = {
        "ensure": lambda _config: calls.append("bootstrap") or True,
        "reconciler": lambda _config: 0,
        "preflight": lambda _config: calls.append("preflight"),
        "preparer": lambda _config: calls.append("prepare"),
        "status_getter": lambda _connection: DbBootstrapStatus.NO_DB,
    }
    assert deployment_init(config, **actions) == (LifecycleOutcome.BOOTSTRAPPED, 0)
    assert calls == ["preflight", "prepare", "bootstrap"]

    failed = {
        "ensure": lambda _config: True,
        "reconciler": lambda _config: 0,
        "preflight": lambda _config: (_ for _ in ()).throw(RuntimeError("bad inputs")),
        "status_getter": lambda _connection: DbBootstrapStatus.NO_DB,
    }
    with pytest.raises(RuntimeError, match="bad inputs"):
        deployment_init(_config(tmp_path / "failed"), **failed)
    assert not runtime_readiness_marker(tmp_path / "failed" / "data", "runtime").exists()


def test_init_seeds_runs_restore_hooks_then_reconciles(tmp_path: Path) -> None:
    config = _config(tmp_path)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "10_restore.py").write_text("pass\n")
    status = DbBootstrapStatus.NO_DB
    calls: list[str] = []

    def seed(_config: GodooConfig) -> None:
        nonlocal status
        calls.append("seed")
        status = DbBootstrapStatus.BOOTSTRAPPED

    attempts = 0

    def post_restore(_config: GodooConfig) -> None:
        nonlocal attempts
        attempts += 1
        calls.append(f"preflight-{attempts}")
        if attempts == 1:
            message = "dependencies unavailable"
            raise RuntimeError(message)

    actions = {
        "ensure": lambda _config: False,
        "seeder": seed,
        "reconciler": lambda _config: calls.append("reconcile") or 0,
        "status_getter": lambda _connection: status,
        "hook_runner": lambda _config, _script: calls.append("restore-hook") or 0,
        "post_restore_preflight": post_restore,
    }
    with pytest.raises(RuntimeError, match="dependencies unavailable"):
        deployment_init(
            config,
            **actions,
            seed_requested=True,
            after_restore_dirs=[hooks],
        )
    assert calls == ["seed", "preflight-1"]
    result = deployment_init(
        config,
        **actions,
        after_restore_dirs=[hooks],
    )
    assert result == (LifecycleOutcome.READY, 0)
    assert calls == ["seed", "preflight-1", "preflight-2", "restore-hook", "reconcile"]


def test_existing_runtime_reconciles_without_bootstrap(tmp_path: Path) -> None:
    calls: list[str] = []
    result = deployment_init(
        _config(tmp_path),
        ensure=lambda _config: pytest.fail("must not bootstrap"),
        reconciler=lambda _config: calls.append("reconcile") or 0,
        status_getter=lambda _connection: DbBootstrapStatus.BOOTSTRAPPED,
    )
    assert result == (LifecycleOutcome.READY, 0)
    assert calls == ["reconcile"]
