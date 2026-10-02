"""Composable lifecycle orchestration for a gOdoo runtime."""

import logging
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import cast

from ..database.connection import DBConnection
from ..database.state import DbBootstrapStatus, classify_bootstrap_state
from ..models import GodooConfig
from .locks import (
    begin_runtime_lifecycle,
    finish_runtime_lifecycle,
    read_runtime_lifecycle,
    runtime_inconsistency_reason,
    runtime_locks,
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
HookRunner = Callable[[GodooConfig, Path], int]


class LifecycleOutcome(StrEnum):
    """Stable result of selecting a runtime initialization path."""

    READY = "ready"
    BOOTSTRAPPED = "bootstrapped"
    RESTORED = "restored"


class LifecycleBootstrapError(RuntimeError):
    """Raised when Odoo's native bootstrap command fails."""

    def __init__(self, db_name: str, return_code: int) -> None:
        """Record the database and Odoo process status that failed."""
        super().__init__(f"Odoo bootstrap failed for database '{db_name}' (exit code {return_code})")
        self.return_code = return_code


def validate_initialization_state(
    config: GodooConfig, status: DbBootstrapStatus, *, allow_lifecycle_retry: bool = False
) -> None:
    """Reject non-Odoo databases and orphaned filestores before initialization."""
    reason = runtime_inconsistency_reason(
        config.data_dir,
        config.db_name,
        missing_or_empty=status in (DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB),
    )
    if reason == "an unfinished restore promotion":
        message = (
            f"Runtime '{config.db_name}' has an unfinished restore promotion. "
            "Recover its matching database and filestore before removing the pending restore marker."
        )
        raise ValueError(message)
    if reason == "unresolved legacy lifecycle marker":
        msg = f"Runtime '{config.db_name}' has unresolved legacy lifecycle work; recover it manually before initialization."
        raise ValueError(msg)
    if reason == "unfinished lifecycle work" and not allow_lifecycle_retry:
        message = f"Runtime '{config.db_name}' has unfinished lifecycle work; rerun runtime init to resume it."
        raise ValueError(message)
    if status == DbBootstrapStatus.INVALID_DB:
        message = f"Runtime '{config.db_name}' has an invalid database; inspect it before initializing."
        raise ValueError(message)
    orphaned_filestore = config.data_dir / "filestore" / config.db_name
    has_orphaned_filestore = (
        status in (DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB)
        and orphaned_filestore.exists()
        and (not orphaned_filestore.is_dir() or next(orphaned_filestore.iterdir(), None) is not None)
    )
    if reason == "filestore data but no initialized database" or has_orphaned_filestore:
        message = (
            f"Runtime '{config.db_name}' has filestore data but no initialized database. "
            "Recover the matching database and filestore before initializing."
        )
        raise ValueError(message)


def ensure_runtime(
    godoo_config: GodooConfig,
    *,
    preparer: RuntimePreparer = prepare_runtime,
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


def run_hook_directories(config: GodooConfig, directories: list[Path], runner: HookRunner) -> int:
    """Run hook directories in supplied order and Python files in lexical order."""
    for directory in directories:
        if not directory.is_dir():
            message = f"Lifecycle hook directory does not exist: {directory}"
            raise ValueError(message)
        for script in sorted(directory.glob("*.py")):
            LOGGER.info("Running lifecycle hook %s", script)
            result = runner(config, script)
            if result:
                return result
    return 0


def reconcile_runtime(
    config: GodooConfig,
    *,
    preparer: RuntimePreparer = prepare_runtime,
    status_getter: BootstrapStatusGetter = classify_bootstrap_state,
    reconciler: RuntimeReconciler | None = None,
    after_reconcile_dirs: list[Path] | None = None,
    hook_runner: HookRunner | None = None,
    allow_lifecycle_retry: bool = False,
) -> int:
    """Prepare and update an existing runtime without bootstrapping or resetting it."""
    with runtime_locks(config.data_dir, config.db_name):
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
        if hook_runner:
            return run_hook_directories(config, after_reconcile_dirs or [], hook_runner)
        return 0


def _initialize_runtime(
    config: GodooConfig,
    *,
    seed_requested: bool,
    seeder: Callable[[GodooConfig], None] | None,
    ensure: Callable[[GodooConfig], bool],
    status_getter: BootstrapStatusGetter,
    allow_lifecycle_retry: bool = False,
) -> LifecycleOutcome:
    """Select and execute exactly one state-initialization path."""
    status = status_getter(config.db_connection)
    validate_initialization_state(config, status, allow_lifecycle_retry=allow_lifecycle_retry)
    if status == DbBootstrapStatus.BOOTSTRAPPED:
        if seed_requested:
            LOGGER.info("Runtime '%s' is already ready; skipping configured seed artifacts", config.db_name)
        return LifecycleOutcome.READY
    if status not in (DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB):
        message = f"Unsupported runtime state for '{config.db_name}': {status}"
        raise ValueError(message)
    if not seed_requested:
        ensure(config)
        return LifecycleOutcome.BOOTSTRAPPED
    if seeder is None:
        message = "Runtime seeding was requested but no complete seed was supplied."
        raise ValueError(message)
    LOGGER.info("Seeding missing or empty runtime '%s'", config.db_name)
    seeder(config)
    return LifecycleOutcome.RESTORED


def _run_outcome_hooks(
    config: GodooConfig,
    outcome: LifecycleOutcome,
    *,
    after_bootstrap_dirs: list[Path],
    after_restore_dirs: list[Path],
    hook_runner: HookRunner | None,
) -> int:
    """Run only the hook phase selected by the preserved initialization outcome."""
    if hook_runner is None:
        return 0
    if outcome == LifecycleOutcome.BOOTSTRAPPED:
        return run_hook_directories(config, after_bootstrap_dirs, hook_runner)
    if outcome == LifecycleOutcome.RESTORED:
        return run_hook_directories(config, after_restore_dirs, hook_runner)
    return 0


def deployment_init(  # noqa: C901
    config: GodooConfig,
    *,
    seed_requested: bool,
    seeder: Callable[[GodooConfig], None] | None,
    ensure: Callable[[GodooConfig], bool],
    reconciler: RuntimeReconciler,
    preparer: RuntimePreparer | None = None,
    preflight: RuntimePreparer | None = None,
    post_restore_preflight: RuntimePreparer | None = None,
    status_getter: BootstrapStatusGetter = classify_bootstrap_state,
    after_bootstrap_dirs: list[Path] | None = None,
    after_restore_dirs: list[Path] | None = None,
    after_reconcile_dirs: list[Path] | None = None,
    hook_runner: HookRunner | None = None,
    lifecycle_plan: dict[str, object] | None = None,
    adopt_legacy_plan: bool = False,
) -> tuple[LifecycleOutcome, int]:
    """Run lock-owned lifecycle phases, persisting each pending phase before entry."""
    # Serialize recovery and reconciliation so every marker describes one complete attempt.
    with runtime_locks(config.data_dir, config.db_name):
        require_runtime_database_major(config)
        plan = lifecycle_plan or build_runtime_lifecycle_plan(
            config,
            runtime_version=None,
            expected_odoo_major=None,
            seed=None,
            seed_requested=seed_requested,
            original_filestore=None,
            update_modules=None,
            install_modules=None,
            upgrade_paths=None,
            pre_upgrade_scripts=None,
            after_bootstrap_dirs=after_bootstrap_dirs,
            after_restore_dirs=after_restore_dirs,
            after_reconcile_dirs=after_reconcile_dirs,
            install_base_modules=True,
            install_workspace_modules=True,
            odoo_demo=False,
        )
        marker = begin_runtime_lifecycle(
            config.data_dir,
            config.db_name,
            plan=plan,
            adopt_legacy_plan=adopt_legacy_plan,
        )
        state = read_runtime_lifecycle(marker, config.db_name)
        assert state is not None
        phase = cast(str, state["pending_phase"])
        outcome_value = cast(str, state["outcome"])
        initial_status = status_getter(config.db_connection)
        if preparer:
            preparer(config)
        if preflight:
            preflight(config)
        outcome = (
            LifecycleOutcome(outcome_value) if outcome_value in {item.value for item in LifecycleOutcome} else None
        )
        if phase == "initialize":
            status = initial_status
            if outcome is None and status is not DbBootstrapStatus.BOOTSTRAPPED:
                outcome = LifecycleOutcome.RESTORED if seed_requested else LifecycleOutcome.BOOTSTRAPPED
                write_runtime_lifecycle(marker, config.db_name, outcome=outcome.value, pending_phase=phase, plan=plan)
            if outcome is not None and status is DbBootstrapStatus.BOOTSTRAPPED:
                phase = "after-restore" if outcome is LifecycleOutcome.RESTORED else "after-bootstrap"
            else:
                outcome = _initialize_runtime(
                    config,
                    seed_requested=seed_requested,
                    seeder=seeder,
                    ensure=ensure,
                    status_getter=status_getter,
                    allow_lifecycle_retry=True,
                )
                phase = "after-restore" if outcome is LifecycleOutcome.RESTORED else "after-bootstrap"
            write_runtime_lifecycle(marker, config.db_name, outcome=outcome.value, pending_phase=phase, plan=plan)
        assert outcome is not None
        if phase == "after-bootstrap":
            if outcome is LifecycleOutcome.BOOTSTRAPPED:
                result = _run_outcome_hooks(
                    config,
                    outcome,
                    after_bootstrap_dirs=after_bootstrap_dirs or [],
                    after_restore_dirs=[],
                    hook_runner=hook_runner,
                )
                if result:
                    return outcome, result
            phase = "reconcile"
            write_runtime_lifecycle(marker, config.db_name, outcome=outcome.value, pending_phase=phase, plan=plan)
        if phase == "after-restore":
            if outcome is LifecycleOutcome.RESTORED:
                if post_restore_preflight:
                    post_restore_preflight(config)
                result = _run_outcome_hooks(
                    config,
                    outcome,
                    after_bootstrap_dirs=[],
                    after_restore_dirs=after_restore_dirs or [],
                    hook_runner=hook_runner,
                )
                if result:
                    return outcome, result
            phase = "reconcile"
            write_runtime_lifecycle(marker, config.db_name, outcome=outcome.value, pending_phase=phase, plan=plan)
        if phase == "reconcile":
            result = reconciler(config)
            if result:
                return outcome, result
            phase = "after-reconcile"
            write_runtime_lifecycle(marker, config.db_name, outcome=outcome.value, pending_phase=phase, plan=plan)
        if phase == "after-reconcile" and hook_runner:
            result = run_hook_directories(config, after_reconcile_dirs or [], hook_runner)
            if result:
                return outcome, result
        finish_runtime_lifecycle(marker)
        return outcome, 0


def run_lifecycle_hook(config: GodooConfig, script: Path) -> int:
    """Execute a deployment policy hook through the Odoo shell command."""
    command = build_odoo_shell_command(config)
    return run_odoo_command(command, input=script.read_text(encoding="utf-8"), text=True).returncode


def split_lifecycle_values(values: list[str] | None) -> list[str]:
    """Flatten repeatable comma-separated values, preserving first-seen order."""
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


def _path_identity(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def build_runtime_lifecycle_plan(
    config: GodooConfig,
    *,
    runtime_version: str | None,
    expected_odoo_major: int | None,
    seed: Path | None,
    seed_requested: bool,
    original_filestore: Path | None,
    update_modules: list[str] | None,
    install_modules: list[str] | None,
    upgrade_paths: list[Path] | Path | None,
    pre_upgrade_scripts: list[Path] | None,
    after_bootstrap_dirs: list[Path] | None,
    after_restore_dirs: list[Path] | None,
    after_reconcile_dirs: list[Path] | None,
    install_base_modules: bool,
    install_workspace_modules: bool,
    odoo_demo: bool,
) -> dict[str, object]:
    """Build a nonsecret semantic identity for resumable runtime initialization."""
    hook_dirs = {
        "after_bootstrap": after_bootstrap_dirs or [],
        "after_restore": after_restore_dirs or [],
        "after_reconcile": after_reconcile_dirs or [],
    }
    selected_hooks: dict[str, list[dict[str, object]]] = {}
    script_diagnostics: dict[str, list[dict[str, object]]] = {}
    for phase, directories in hook_dirs.items():
        phase_directories: list[dict[str, object]] = []
        phase_diagnostics: list[dict[str, object]] = []
        for directory in directories:
            resolved = directory.expanduser().resolve()
            if not resolved.is_dir():
                message = f"Lifecycle hook directory does not exist: {resolved}"
                raise ValueError(message)
            scripts = sorted(resolved.glob("*.py"))
            phase_directories.append({"directory": str(resolved), "scripts": [script.name for script in scripts]})
            phase_diagnostics.extend(
                {
                    "path": str(script.resolve()),
                    "size": script.stat().st_size,
                    "mtime_ns": script.stat().st_mtime_ns,
                }
                for script in scripts
            )
        selected_hooks[phase] = phase_directories
        script_diagnostics[phase] = phase_diagnostics

    normalized_pre_scripts = [path.expanduser().resolve() for path in pre_upgrade_scripts or []]
    for script in normalized_pre_scripts:
        if not script.is_file():
            message = f"Pre-upgrade script does not exist: {script}"
            raise ValueError(message)
    script_diagnostics["pre_upgrade"] = [
        {"path": str(script), "size": script.stat().st_size, "mtime_ns": script.stat().st_mtime_ns}
        for script in normalized_pre_scripts
    ]

    connection = config.db_connection
    upgrade_roots = split_upgrade_paths(upgrade_paths)
    identity: dict[str, object] = {
        "runtime": {
            "install_path": str(config.odoo_install_folder.resolve()),
            "addon_paths": [str(path.expanduser().resolve()) for path in config.addon_paths],
            "version": runtime_version,
            "expected_major": expected_odoo_major,
        },
        "database": {
            "name": config.db_name,
            "filter": config.db_filter,
            "data_dir": str(config.data_dir.resolve()),
            "connection": {
                "host": connection.hostname,
                "port": connection.port,
                "user": connection.username,
                "sslmode": connection.sslmode,
            },
        },
        "seed_requested": seed_requested,
        "artifacts": {
            "seed": _path_identity(seed),
            "original_filestore": _path_identity(original_filestore),
        },
        "modules": {
            "update": split_lifecycle_values(update_modules),
            "install": split_lifecycle_values(install_modules),
            "install_base": install_base_modules,
            "install_workspace": install_workspace_modules,
            "demo": odoo_demo,
        },
        "upgrade_roots": [str(path) for path in upgrade_roots],
        "pre_upgrade_scripts": [str(path) for path in normalized_pre_scripts],
        "hooks": selected_hooks,
    }
    return {"identity": identity, "diagnostics": {"script_stats": script_diagnostics}}


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
