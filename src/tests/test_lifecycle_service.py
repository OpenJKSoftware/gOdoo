"""Tests for the state-based runtime lifecycle service."""

import logging
from pathlib import Path

import pytest

from godoo_cli.commands.db.query import DbBootstrapStatus
from godoo_cli.lifecycle import LifecycleOutcome, deployment_init, run_hook_directories, run_hook_directory
from godoo_cli.models import GodooConfig


def _config(tmp_path: Path) -> GodooConfig:
    return GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        db_name="runtime",
    )


def test_deployment_init_bootstraps_then_reconciles(tmp_path: Path):
    calls: list[str] = []
    result = deployment_init(
        _config(tmp_path),
        seed_requested=False,
        seeder=None,
        status_getter=lambda _connection: DbBootstrapStatus.NO_DB,
        ensure=lambda _config: calls.append("bootstrap") or True,
        reconciler=lambda _config: calls.append("reconcile") or 0,
    )
    assert result == (LifecycleOutcome.BOOTSTRAPPED, 0)
    assert calls == ["bootstrap", "reconcile"]


def test_deployment_init_seeds_then_reconciles_without_bootstrap(tmp_path: Path):
    calls: list[str] = []
    result = deployment_init(
        _config(tmp_path),
        seed_requested=True,
        status_getter=lambda _connection: DbBootstrapStatus.NO_DB,
        seeder=lambda _config: calls.append("seed"),
        ensure=lambda _config: calls.append("bootstrap") or True,
        reconciler=lambda _config: calls.append("reconcile") or 0,
    )
    assert result == (LifecycleOutcome.RESTORED, 0)
    assert calls == ["seed", "reconcile"]


def test_deployment_init_syncs_prepares_seeds_and_runs_phase_hooks(tmp_path: Path):
    calls: list[str] = []
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "10_policy.py").write_text("policy")

    result = deployment_init(
        _config(tmp_path),
        seed_requested=True,
        seeder=lambda _config: calls.append("seed"),
        ensure=lambda _config: False,
        source_synchronizer=lambda: calls.append("sync"),
        preparer=lambda _config: calls.append("prepare"),
        status_getter=lambda _connection: DbBootstrapStatus.NO_DB,
        reconciler=lambda _config: calls.append("reconcile") or 0,
        after_restore_dirs=[hooks],
        after_reconcile_dirs=[hooks],
        hook_runner=lambda _config, _script: calls.append("hook") or 0,
    )

    assert result == (LifecycleOutcome.RESTORED, 0)
    assert calls == ["sync", "prepare", "seed", "hook", "reconcile", "hook"]


def test_deployment_init_existing_runtime_only_reconciles(tmp_path: Path):
    calls: list[str] = []
    result = deployment_init(
        _config(tmp_path),
        seed_requested=False,
        seeder=None,
        status_getter=lambda _connection: DbBootstrapStatus.BOOTSTRAPPED,
        ensure=lambda _config: calls.append("bootstrap") or True,
        reconciler=lambda _config: calls.append("reconcile") or 0,
    )
    assert result == (LifecycleOutcome.READY, 0)
    assert calls == ["reconcile"]


def test_hook_directory_is_lexical(tmp_path: Path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "20_second.py").write_text("second")
    (hooks / "10_first.py").write_text("first")
    calls: list[str] = []
    result = run_hook_directory(_config(tmp_path), hooks, lambda _config, script: calls.append(script.name) or 0)
    assert result == 0
    assert calls == ["10_first.py", "20_second.py"]


def test_hook_directories_preserve_directory_order(tmp_path: Path):
    second = tmp_path / "second"
    first = tmp_path / "first"
    second.mkdir()
    first.mkdir()
    (second / "10_hook.py").write_text("second")
    (first / "10_hook.py").write_text("first")
    calls: list[str] = []

    result = run_hook_directories(
        _config(tmp_path), [second, first], lambda _config, script: calls.append(script.read_text()) or 0
    )

    assert result == 0
    assert calls == ["second", "first"]


def test_deployment_init_retries_hooks_for_an_existing_runtime(tmp_path: Path):
    hooks = tmp_path / "after-reconcile"
    hooks.mkdir()
    (hooks / "10_policy.py").write_text("policy")
    status = DbBootstrapStatus.NO_DB
    calls: list[str] = []

    def runtime_status(_connection: object) -> DbBootstrapStatus:
        return status

    def ensure(_config: GodooConfig) -> bool:
        nonlocal status
        calls.append("bootstrap")
        status = DbBootstrapStatus.BOOTSTRAPPED
        return True

    hook_attempts = 0

    def run_hook(_config: GodooConfig, _script: Path) -> int:
        nonlocal hook_attempts
        hook_attempts += 1
        calls.append(f"hook-{hook_attempts}")
        return 9 if hook_attempts == 1 else 0

    first_result = deployment_init(
        _config(tmp_path),
        seed_requested=False,
        seeder=None,
        status_getter=runtime_status,
        ensure=ensure,
        reconciler=lambda _config: calls.append("reconcile") or 0,
        after_reconcile_dirs=[hooks],
        hook_runner=run_hook,
    )
    second_result = deployment_init(
        _config(tmp_path),
        seed_requested=False,
        seeder=None,
        status_getter=runtime_status,
        ensure=ensure,
        reconciler=lambda _config: calls.append("reconcile") or 0,
        after_reconcile_dirs=[hooks],
        hook_runner=run_hook,
    )

    assert first_result == (LifecycleOutcome.BOOTSTRAPPED, 9)
    assert second_result == (LifecycleOutcome.READY, 0)
    assert calls == ["bootstrap", "reconcile", "hook-1", "reconcile", "hook-2"]


def test_deployment_init_skips_configured_seed_for_ready_runtime(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    calls: list[str] = []
    caplog.set_level(logging.INFO)

    result = deployment_init(
        _config(tmp_path),
        seed_requested=True,
        seeder=lambda _config: calls.append("seed"),
        status_getter=lambda _connection: DbBootstrapStatus.BOOTSTRAPPED,
        ensure=lambda _config: calls.append("bootstrap") or True,
        reconciler=lambda _config: calls.append("reconcile") or 0,
    )

    assert result == (LifecycleOutcome.READY, 0)
    assert calls == ["reconcile"]
    assert "skipping configured seed artifacts" in caplog.text
