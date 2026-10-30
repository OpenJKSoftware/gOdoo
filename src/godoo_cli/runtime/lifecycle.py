"""Runtime initialization and reconciliation helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from ..database.connection import DBConnection
from ..database.state import DbBootstrapStatus, classify_bootstrap_state
from ..models import GodooConfig
from .baseline import baseline_database_name
from .hooks import HookDefinition, run_hook_phase
from .locks import (
    begin_runtime_lifecycle,
    finish_runtime_lifecycle,
    read_runtime_lifecycle,
    runtime_inconsistency_reason,
    runtime_locks,
    runtime_readiness_marker,
    write_runtime_lifecycle,
)
from .odoo import (
    bootstrap_runtime,
    build_launch_command,
    build_odoo_shell_command,
    preflight_for_config,
    prepare_runtime,
    require_runtime_database_major,
    run_odoo_command,
)

LOGGER = logging.getLogger(__name__)
RuntimePreparer = Callable[[GodooConfig], None]
BootstrapStatusGetter = Callable[[DBConnection], DbBootstrapStatus]
RuntimeBootstrapper = Callable[..., int]
RuntimeReconciler = Callable[[GodooConfig], int]


class LifecycleOutcome(StrEnum):
    """Stable result selecting the runtime initialization path."""

    READY = "ready"
    BOOTSTRAPPED = "bootstrapped"
    RESTORED = "restored"


class LifecycleBootstrapError(RuntimeError):
    """Raised when Odoo's native bootstrap command fails."""

    def __init__(self, db_name: str, return_code: int) -> None:
        """Record the database and native Odoo exit status."""
        super().__init__(f"Odoo bootstrap failed for database '{db_name}' (exit code {return_code})")
        self.return_code = return_code


def validate_initialization_state(
    config: GodooConfig, status: DbBootstrapStatus, *, allow_lifecycle_retry: bool = False
) -> None:
    """Reject incomplete database/filestore pairs and invalid databases before init."""
    reason = runtime_inconsistency_reason(
        config.data_dir,
        config.db_name,
        missing_or_empty=status in {DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB},
    )
    if reason == "unfinished lifecycle work":
        if not allow_lifecycle_retry:
            message = f"Runtime '{config.db_name}' has incomplete initialization; rerun runtime init."
            raise ValueError(message)
    elif reason is not None:
        message = (
            f"Runtime '{config.db_name}' has {reason}. "
            "Restore or reset its matching database and filestore before initializing."
        )
        raise ValueError(message)
    if status == DbBootstrapStatus.INVALID_DB:
        message = f"Runtime '{config.db_name}' has an invalid database; inspect it before initializing."
        raise ValueError(message)


# Preserve the bootstrap service API: native policy flags and independently injectable lifecycle operations.
# pylint: disable-next=too-many-arguments
def ensure_runtime(
    godoo_config: GodooConfig,
    *,
    preparer: RuntimePreparer | None = prepare_runtime,
    status_getter: BootstrapStatusGetter = classify_bootstrap_state,
    bootstrapper: RuntimeBootstrapper = bootstrap_runtime,
    odoo_demo: bool = False,
    extra_bootstrap_args: list[str] | None = None,
    install_workspace_modules: bool = True,
    install_base_modules: bool = True,
    allow_lifecycle_retry: bool = False,
) -> bool:
    """Ensure a prepared Odoo runtime exists, returning whether it was created.

    Odoo remains authoritative for bootstrap/database creation: only a missing
    or empty database is passed to its native initialization command.

    Returns:
        Whether this operation bootstrapped a new runtime.

    Raises:
        ValueError: If the existing database is not a usable Odoo runtime.
        LifecycleBootstrapError: If Odoo fails to initialize the database.
    """
    with runtime_locks(godoo_config.data_dir, godoo_config.db_name):
        if preparer:
            preparer(godoo_config)
        status = status_getter(godoo_config.db_connection)
        validate_initialization_state(godoo_config, status, allow_lifecycle_retry=allow_lifecycle_retry)
        LOGGER.info("Bootstrap status for database '%s': %s", godoo_config.db_name, status.value)
        if status == DbBootstrapStatus.BOOTSTRAPPED:
            return False
        if status not in (DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB):
            msg = f"Unsupported bootstrap status for database '{godoo_config.db_name}': {status}"
            raise ValueError(msg)

        bootstrap_args = list(extra_bootstrap_args or [])
        if not odoo_demo:
            bootstrap_args.append("--without-demo")
        return_code = bootstrapper(
            godoo_config,
            extra_cmd_args=bootstrap_args,
            install_workspace_modules=install_workspace_modules,
            install_base_modules=install_base_modules,
        )
        if return_code:
            raise LifecycleBootstrapError(godoo_config.db_name, return_code)
        return True


def reconcile_runtime(
    config: GodooConfig,
    *,
    preparer: RuntimePreparer | None = prepare_runtime,
    status_getter: BootstrapStatusGetter = classify_bootstrap_state,
    reconciler: RuntimeReconciler | None = None,
    allow_lifecycle_retry: bool = False,
) -> int:
    """Prepare and update an existing runtime without bootstrapping or resetting it."""
    with runtime_locks(config.data_dir, config.db_name):
        if preparer:
            preparer(config)
        status = status_getter(config.db_connection)
        validate_initialization_state(config, status, allow_lifecycle_retry=allow_lifecycle_retry)
        if status != DbBootstrapStatus.BOOTSTRAPPED:
            message = f"Runtime '{config.db_name}' is not bootstrapped; reconcile never initializes a database."
            raise ValueError(message)
        if reconciler:
            result = reconciler(config)
            if result:
                return result
        return 0


def _initialize_runtime(
    config: GodooConfig,
    *,
    seed_requested: bool,
    seeder: Callable[[GodooConfig], None] | None,
    ensure: Callable[[GodooConfig], bool],
    status_getter: BootstrapStatusGetter,
) -> LifecycleOutcome:
    status = status_getter(config.db_connection)
    validate_initialization_state(config, status, allow_lifecycle_retry=True)
    if status == DbBootstrapStatus.BOOTSTRAPPED:
        return LifecycleOutcome.READY
    if status not in {DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB}:
        message = f"Unsupported runtime state '{config.db_name}': {status}"
        raise ValueError(message)
    outcome = LifecycleOutcome.RESTORED if seed_requested else LifecycleOutcome.BOOTSTRAPPED
    write_runtime_lifecycle(
        runtime_readiness_marker(config.data_dir, config.db_name), config.db_name, outcome=outcome.value
    )
    if seed_requested:
        if seeder is None:
            message = "Runtime seeding requested but no complete seed supplied."
            raise ValueError(message)
        seeder(config)
        return LifecycleOutcome.RESTORED
    ensure(config)
    return LifecycleOutcome.BOOTSTRAPPED


# Preserve the lifecycle service API and its independently injectable restore, reconciliation, and recovery steps.
# pylint: disable-next=too-many-arguments
def deployment_init(
    config: GodooConfig,
    *,
    seed_requested: bool = False,
    seeder: Callable[[GodooConfig], None] | None = None,
    ensure: Callable[[GodooConfig], bool],
    reconciler: RuntimeReconciler,
    preparer: RuntimePreparer | None = None,
    preflight: RuntimePreparer | None = None,
    seed_preflight: RuntimePreparer | None = None,
    post_restore_preflight: RuntimePreparer | None = None,
    hooks: list[HookDefinition] | None = None,
    environment: str | None = None,
    upgrade_path: list[Path] | Path | None = None,
    status_getter: BootstrapStatusGetter = classify_bootstrap_state,
    keep_reset_baseline: bool = False,
) -> tuple[LifecycleOutcome, int]:
    """Run one explicit initialization attempt and clear the marker only on success."""
    lock_names = (config.db_name, baseline_database_name(config.db_name)) if keep_reset_baseline else (config.db_name,)
    with runtime_locks(config.data_dir, *lock_names):
        require_runtime_database_major(config)
        status = status_getter(config.db_connection)
        validate_initialization_state(config, status, allow_lifecycle_retry=True)
        if preflight:
            preflight(config)
        if seed_requested and status in {DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB} and seed_preflight:
            seed_preflight(config)
        existing = read_runtime_lifecycle(runtime_readiness_marker(config.data_dir, config.db_name), config.db_name)
        marker = begin_runtime_lifecycle(
            config.data_dir, config.db_name, outcome=existing["outcome"] if existing else "ready"
        )
        if preparer:
            preparer(config)
        outcome = _initialize_runtime(
            config, seed_requested=seed_requested, seeder=seeder, ensure=ensure, status_getter=status_getter
        )
        persisted = read_runtime_lifecycle(marker, config.db_name)
        hook_outcome = (
            LifecycleOutcome(persisted["outcome"]) if persisted and persisted["outcome"] != "ready" else outcome
        )
        if outcome != LifecycleOutcome.READY:
            write_runtime_lifecycle(marker, config.db_name, outcome=outcome.value)
            hook_outcome = outcome
        if hook_outcome == LifecycleOutcome.RESTORED and post_restore_preflight:
            post_restore_preflight(config)
        phase = {
            LifecycleOutcome.BOOTSTRAPPED: "after_bootstrap",
            LifecycleOutcome.RESTORED: "after_restore",
        }.get(hook_outcome)
        result = (
            run_hook_phase(
                config,
                hooks or [],
                phase=phase,
                outcome=hook_outcome.value,
                environment=environment,
                upgrade_path=upgrade_path,
            )
            if phase is not None
            else 0
        )
        if result:
            return outcome, result
        result = reconciler(config)
        if result:
            return outcome, result
        result = run_hook_phase(
            config,
            hooks or [],
            phase="after_reconcile",
            outcome=hook_outcome.value,
            environment=environment,
            upgrade_path=upgrade_path,
        )
        if result:
            return outcome, result
        finish_runtime_lifecycle(marker)
        return outcome, 0


def run_lifecycle_hook(config: GodooConfig, script: Path) -> int:
    """Execute a deployment hook through Odoo shell."""
    return run_odoo_command(
        build_odoo_shell_command(config), input=script.read_text(encoding="utf-8"), text=True
    ).returncode


def split_lifecycle_values(values: list[str] | None) -> list[str]:
    """Flatten comma-separated selections while preserving first occurrence."""
    return list(dict.fromkeys(item.strip() for value in values or [] for item in value.split(",") if item.strip()))


def split_upgrade_paths(values: list[Path] | Path | None) -> list[Path]:
    """Normalize repeatable and comma-separated native upgrade roots."""
    selected: list[Path] = []
    seen: set[Path] = set()
    raw_values = [values] if isinstance(values, Path) else values or []
    for value in raw_values:
        for item in str(value).split(","):
            if not item.strip():
                continue
            path = Path(item.strip()).expanduser().resolve()
            if path not in seen:
                selected.append(path)
                seen.add(path)
    return selected


def preflight_reconcile_dependencies(
    config: GodooConfig,
    update_modules: list[str] | None,
    install_modules: list[str] | None,
    *,
    ignore_missing_installed_modules: bool = False,
) -> None:
    """Include explicitly selected modules in dependency preflight."""
    arguments: list[str] = []
    updates = split_lifecycle_values(update_modules)
    installs = split_lifecycle_values(install_modules)
    if updates:
        arguments.extend(["--update", ",".join(updates)])
    if installs:
        arguments.extend(["--init", ",".join(installs)])
    preflight_for_config(
        config,
        arguments,
        ignore_missing_installed_modules=ignore_missing_installed_modules,
    )


def reconcile_modules(
    config: GodooConfig,
    update_modules: list[str] | None,
    install_modules: list[str] | None,
    *,
    upgrade_path: list[Path] | Path | None = None,
    pre_upgrade_scripts: list[Path] | None = None,
    log_handlers: list[str] | None = None,
) -> int:
    """Run explicitly requested module actions and stop; no action is implicit."""
    require_runtime_database_major(config)
    updates = split_lifecycle_values(update_modules)
    installs = split_lifecycle_values(install_modules)
    scripts = list(dict.fromkeys(pre_upgrade_scripts or []))
    handlers = split_lifecycle_values(log_handlers)
    upgrade_paths = split_upgrade_paths(upgrade_path)
    if (upgrade_paths or scripts) and not updates:
        message = "--upgrade-path and --pre-upgrade-script require at least one --update module."
        raise ValueError(message)
    extra: list[str] = ["--stop-after-init"]
    if updates:
        extra.extend(["--update", ",".join(updates)])
    if installs:
        extra.extend(["--init", ",".join(installs)])
    if not updates and not installs:
        return 0
    if upgrade_paths:
        extra.extend(["--upgrade-path", ",".join(str(path) for path in upgrade_paths)])
    if scripts:
        extra.extend(["--pre-upgrade-scripts", ",".join(str(script) for script in scripts)])
    for handler in handlers:
        extra.extend(["--log-handler", handler])
    if not config.odoo_conf_path.exists():
        extra.extend(["--addons-path", ",".join(str(path.absolute()) for path in config.addon_paths)])
    extra.append("--no-http")
    return run_odoo_command(
        build_launch_command(config, extra, upgrade_workspace_modules=False, save_config=False)
    ).returncode
