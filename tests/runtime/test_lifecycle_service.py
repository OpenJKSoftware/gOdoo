"""Tests for the state-based runtime lifecycle service."""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import TypedDict, Unpack

import pytest

from godoo_cli.commands.runtime import init as lifecycle_commands
from godoo_cli.database.state import DbBootstrapStatus
from godoo_cli.models import GodooConfig
from godoo_cli.runtime import lifecycle as runtime_lifecycle
from godoo_cli.runtime.lifecycle import (
    HookRunner,
    LifecycleOutcome,
    RuntimePreparer,
    RuntimeReconciler,
    deployment_init,
    ensure_runtime,
    reconcile_runtime,
    run_hook_directories,
)
from godoo_cli.runtime.locks import runtime_readiness_marker

LOGGER = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def supported_cli_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lifecycle_commands,
        "require_odoo_version",
        lambda *_args: SimpleNamespace(major=19, raw="19.0"),
    )
    monkeypatch.setattr(runtime_lifecycle, "require_runtime_database_major", lambda _config: 19)


class _EnsureRuntimeKwargs(TypedDict):
    """Arguments supplied by the lifecycle command's ensure callback."""

    preparer: RuntimePreparer
    odoo_demo: bool
    extra_bootstrap_args: list[str] | None
    install_workspace_modules: bool
    install_base_modules: bool
    allow_lifecycle_retry: bool


class _ReconcileRuntimeKwargs(TypedDict):
    """Arguments supplied by the lifecycle command's reconcile callback."""

    preparer: RuntimePreparer
    allow_lifecycle_retry: bool
    reconciler: RuntimeReconciler


class _DeploymentInitKwargs(TypedDict):
    """Arguments supplied by the lifecycle command's deployment call."""

    seed_requested: bool
    seeder: Callable[[GodooConfig], None] | None
    ensure: Callable[[GodooConfig], bool]
    preparer: RuntimePreparer | None
    preflight: RuntimePreparer | None
    post_restore_preflight: RuntimePreparer | None
    reconciler: RuntimeReconciler
    after_bootstrap_dirs: list[Path] | None
    after_restore_dirs: list[Path] | None
    after_reconcile_dirs: list[Path] | None
    hook_runner: HookRunner | None


def _config(tmp_path: Path) -> GodooConfig:
    return GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        db_name="runtime",
        data_dir=tmp_path / "data",
    )


@pytest.mark.parametrize("initial_status", [DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB])
def test_public_deployment_init_real_bootstrap_retry_accepts_pending_marker(
    tmp_path: Path, initial_status: DbBootstrapStatus, monkeypatch: pytest.MonkeyPatch
):
    """Guards public deployment init's real bootstrap retry through its pending marker."""
    import typer
    from typer.testing import CliRunner

    from godoo_cli.commands.runtime import init as lifecycle_commands

    app = typer.Typer()
    app.command()(lifecycle_commands.deployment_init_odoo_runtime)
    data_dir = tmp_path / "data"
    state = {"status": initial_status}
    calls: list[str] = []
    observed_retry: list[bool] = []

    def status_getter(_connection: object) -> DbBootstrapStatus:
        return state["status"]

    def bootstrap(_config: GodooConfig, **_kwargs: object) -> int:
        calls.append("bootstrap")
        state["status"] = DbBootstrapStatus.BOOTSTRAPPED
        return 0

    def injected_ensure(config: GodooConfig, **kwargs: Unpack[_EnsureRuntimeKwargs]) -> bool:
        observed_retry.append(bool(kwargs.get("allow_lifecycle_retry")))
        return ensure_runtime(
            config,
            status_getter=status_getter,
            bootstrapper=bootstrap,
            **kwargs,
        )

    def injected_reconcile(config: GodooConfig, **kwargs: Unpack[_ReconcileRuntimeKwargs]) -> int:
        return reconcile_runtime(
            config,
            preparer=kwargs["preparer"],
            status_getter=status_getter,
            reconciler=lambda _runtime: calls.append("reconcile") or 0,
            allow_lifecycle_retry=kwargs["allow_lifecycle_retry"],
        )

    def injected_init(config: GodooConfig, **kwargs: Unpack[_DeploymentInitKwargs]) -> tuple[LifecycleOutcome, int]:
        return deployment_init(config, status_getter=status_getter, **kwargs)

    monkeypatch.setattr(lifecycle_commands, "ensure_runtime", injected_ensure)
    monkeypatch.setattr(lifecycle_commands, "reconcile_runtime", injected_reconcile)
    monkeypatch.setattr(lifecycle_commands, "deployment_init", injected_init)
    monkeypatch.setattr(lifecycle_commands, "prepare_runtime", lambda _config, **_kwargs: None)
    monkeypatch.setattr(runtime_lifecycle, "preflight_for_config", lambda *_args, **_kwargs: None)
    result = CliRunner().invoke(
        app,
        [],
        env={
            "ODOO_MAIN_FOLDER": str(tmp_path / "odoo"),
            "ODOO_WORKSPACE_ADDON_LOCATION": str(tmp_path / "addons"),
            "ODOO_CONF_PATH": str(tmp_path / "odoo.conf"),
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
            "ODOO_DATA_DIR": str(data_dir),
        },
    )

    assert result.exit_code == 0, result.output
    assert observed_retry == [True]
    assert calls == ["bootstrap", "reconcile"]
    assert not runtime_readiness_marker(data_dir, "runtime").exists()


def test_ensure_runtime_rejects_pending_marker_without_retry_authority(tmp_path: Path):
    """Guards ordinary ensure calls from taking ownership of unfinished init."""
    config = _config(tmp_path)
    marker = runtime_readiness_marker(config.data_dir, config.db_name)
    marker.parent.mkdir(parents=True)
    marker.touch()

    with pytest.raises(ValueError, match="unresolved legacy lifecycle work"):
        ensure_runtime(
            config,
            preparer=lambda _runtime: None,
            status_getter=lambda _connection: DbBootstrapStatus.NO_DB,
            bootstrapper=lambda _config, **_kwargs: 0,
        )


def test_deployment_init_bootstraps_then_reconciles(tmp_path: Path):
    """Guards the contract that deployment init bootstraps then reconciles."""
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


def test_deployment_init_refreshes_config_before_dependency_preflight(tmp_path: Path) -> None:
    """Run preparation before preflight can inspect configuration."""
    calls: list[str] = []

    result = deployment_init(
        _config(tmp_path),
        seed_requested=False,
        seeder=None,
        preparer=lambda _config: calls.append("prepare"),
        preflight=lambda _config: calls.append("preflight"),
        ensure=lambda _config: True,
        status_getter=lambda _connection: DbBootstrapStatus.BOOTSTRAPPED,
        reconciler=lambda _config: calls.append("reconcile") or 0,
    )

    assert result == (LifecycleOutcome.READY, 0)
    assert calls == ["prepare", "preflight", "reconcile"]


def test_deployment_init_seeds_then_reconciles_without_bootstrap(tmp_path: Path):
    """Guards the contract that deployment init seeds then reconciles without bootstrap."""
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


def test_deployment_init_prepares_seeds_and_runs_phase_hooks(tmp_path: Path):
    """Guards the contract that deployment init prepares seeds and runs phase hooks."""
    calls: list[str] = []
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "10_policy.py").write_text("policy")

    result = deployment_init(
        _config(tmp_path),
        seed_requested=True,
        seeder=lambda _config: calls.append("seed"),
        ensure=lambda _config: False,
        preparer=lambda _config: calls.append("prepare"),
        status_getter=lambda _connection: DbBootstrapStatus.NO_DB,
        reconciler=lambda _config: calls.append("reconcile") or 0,
        after_restore_dirs=[hooks],
        after_reconcile_dirs=[hooks],
        hook_runner=lambda _config, _script: calls.append("hook") or 0,
    )

    assert result == (LifecycleOutcome.RESTORED, 0)
    assert calls == ["prepare", "seed", "hook", "reconcile", "hook"]


def test_deployment_init_existing_runtime_only_reconciles(tmp_path: Path):
    """Guards the contract that deployment init existing runtime only reconciles."""
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


def test_hook_directories_preserve_directory_order(tmp_path: Path):
    """Guards the contract that hook directories preserve directory order."""
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
    """Guards the contract that deployment init retries hooks for an existing runtime."""
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
    assert first_result == (LifecycleOutcome.BOOTSTRAPPED, 9)
    marker = runtime_readiness_marker(tmp_path / "data", "runtime")
    assert json.loads(marker.read_text()) == {
        "schema_version": 1,
        "database": "runtime",
        "outcome": "bootstrapped",
        "pending_phase": "after-reconcile",
    }

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

    assert second_result == (LifecycleOutcome.BOOTSTRAPPED, 0)
    assert calls == ["bootstrap", "reconcile", "hook-1", "hook-2"]


def test_restore_preflight_failure_keeps_marker_until_successful_retry(tmp_path: Path):
    """Guards the contract that restore preflight failure keeps marker until successful retry."""
    config = _config(tmp_path)
    state = DbBootstrapStatus.NO_DB
    attempts = 0

    def status(_connection: object) -> DbBootstrapStatus:
        return state

    def seed(_config: GodooConfig) -> None:
        nonlocal state
        state = DbBootstrapStatus.BOOTSTRAPPED

    def post_restore(_config: GodooConfig) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            message = "dependency preflight failed"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="dependency preflight failed"):
        deployment_init(
            config,
            seed_requested=True,
            seeder=seed,
            ensure=lambda _config: True,
            status_getter=status,
            post_restore_preflight=post_restore,
            reconciler=lambda _config: 0,
        )
    marker = runtime_readiness_marker(config.data_dir, config.db_name)
    assert marker.exists()

    outcome, result = deployment_init(
        config,
        seed_requested=False,
        seeder=None,
        ensure=lambda _config: False,
        status_getter=status,
        post_restore_preflight=post_restore,
        reconciler=lambda _config: 0,
    )
    assert (outcome, result) == (LifecycleOutcome.RESTORED, 0)
    assert not marker.exists()
