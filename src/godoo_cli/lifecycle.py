"""Composable lifecycle orchestration for a gOdoo runtime."""

import logging
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from .commands.db.query import DbBootstrapStatus, _is_bootstrapped
from .commands.odoo_bin.bootstrap import bootstrap_runtime
from .commands.odoo_bin.launch import prepare_runtime
from .models import DBConnection, GodooConfig

LOGGER = logging.getLogger(__name__)

SourceSynchronizer = Callable[[], None]
RuntimePreparer = Callable[[GodooConfig], None]
BootstrapStatusGetter = Callable[[DBConnection], DbBootstrapStatus]
RuntimeBootstrapper = Callable[..., int]
RuntimeReconciler = Callable[[GodooConfig], int]
HookRunner = Callable[[GodooConfig, Path], int]


class LifecycleOutcome(str, Enum):
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


def ensure_runtime(
    godoo_config: GodooConfig,
    *,
    source_synchronizer: Optional[SourceSynchronizer] = None,
    preparer: RuntimePreparer = prepare_runtime,
    status_getter: BootstrapStatusGetter = _is_bootstrapped,
    bootstrapper: RuntimeBootstrapper = bootstrap_runtime,
    odoo_demo: bool = False,
    extra_bootstrap_args: Optional[list[str]] = None,
    install_workspace_modules: bool = True,
    install_base_modules: bool = True,
) -> bool:
    """Ensure a prepared Odoo runtime exists, returning whether it was created.

    Source synchronization is optional and supplied as a collaborator so the
    caller controls its manifest/update policy.  Odoo remains authoritative for
    bootstrap/database creation: only a missing or empty database is passed to
    its native initialization command.
    """
    if source_synchronizer:
        LOGGER.info("Synchronizing source before preparing runtime")
        source_synchronizer()

    preparer(godoo_config)
    status = status_getter(godoo_config.db_connection)
    LOGGER.info("Bootstrap status for database '%s': %s", godoo_config.db_name, status.value)
    if status == DbBootstrapStatus.BOOTSTRAPPED:
        return False
    if status not in (DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB):
        msg = f"Unsupported bootstrap status for database '{godoo_config.db_name}': {status}"
        raise ValueError(msg)

    bootstrap_args = list(extra_bootstrap_args or [])
    if not odoo_demo:
        bootstrap_args.extend(["--without-demo", "all"])
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


def run_hook_directory(config: GodooConfig, directory: Optional[Path], runner: HookRunner) -> int:
    """Compatibility wrapper for callers that still supply one hook directory."""
    return run_hook_directories(config, [directory] if directory is not None else [], runner)


def reconcile_runtime(
    config: GodooConfig,
    *,
    source_synchronizer: Optional[SourceSynchronizer] = None,
    preparer: RuntimePreparer = prepare_runtime,
    status_getter: BootstrapStatusGetter = _is_bootstrapped,
    dependency_resolver: Optional[Callable[[GodooConfig], int]] = None,
    reconciler: Optional[RuntimeReconciler] = None,
    after_reconcile_dirs: Optional[list[Path]] = None,
    hook_runner: Optional[HookRunner] = None,
) -> int:
    """Prepare and update an existing runtime without bootstrapping or resetting it."""
    if source_synchronizer:
        source_synchronizer()
    preparer(config)
    if status_getter(config.db_connection) != DbBootstrapStatus.BOOTSTRAPPED:
        message = f"Runtime '{config.db_name}' is not bootstrapped; reconcile never initializes a database."
        raise ValueError(message)
    if dependency_resolver:
        result = dependency_resolver(config)
        if result:
            return result
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
    seeder: Optional[Callable[[GodooConfig], None]],
    ensure: Callable[[GodooConfig], bool],
    status_getter: BootstrapStatusGetter,
) -> LifecycleOutcome:
    """Select and execute exactly one state-initialization path."""
    status = status_getter(config.db_connection)
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
    hook_runner: Optional[HookRunner],
) -> int:
    """Run only the hook phase selected by the preserved initialization outcome."""
    if hook_runner is None:
        return 0
    if outcome == LifecycleOutcome.BOOTSTRAPPED:
        return run_hook_directories(config, after_bootstrap_dirs, hook_runner)
    if outcome == LifecycleOutcome.RESTORED:
        return run_hook_directories(config, after_restore_dirs, hook_runner)
    return 0


def deployment_init(
    config: GodooConfig,
    *,
    seed_requested: bool,
    seeder: Optional[Callable[[GodooConfig], None]],
    ensure: Callable[[GodooConfig], bool],
    reconciler: RuntimeReconciler,
    source_synchronizer: Optional[SourceSynchronizer] = None,
    preparer: Optional[RuntimePreparer] = None,
    status_getter: BootstrapStatusGetter = _is_bootstrapped,
    after_bootstrap_dirs: Optional[list[Path]] = None,
    after_restore_dirs: Optional[list[Path]] = None,
    after_reconcile_dirs: Optional[list[Path]] = None,
    hook_runner: Optional[HookRunner] = None,
) -> tuple[LifecycleOutcome, int]:
    """Seed or bootstrap, reconcile, run phase hooks, and return the selected path."""
    if source_synchronizer:
        LOGGER.info("Synchronizing source before preparing runtime")
        source_synchronizer()
    if preparer:
        LOGGER.info("Preparing runtime before deployment initialization")
        preparer(config)

    outcome = _initialize_runtime(
        config,
        seed_requested=seed_requested,
        seeder=seeder,
        ensure=ensure,
        status_getter=status_getter,
    )
    result = _run_outcome_hooks(
        config,
        outcome,
        after_bootstrap_dirs=after_bootstrap_dirs or [],
        after_restore_dirs=after_restore_dirs or [],
        hook_runner=hook_runner,
    )
    if result:
        return outcome, result
    result = reconciler(config)
    if result:
        return outcome, result
    if hook_runner:
        result = run_hook_directories(config, after_reconcile_dirs or [], hook_runner)
        if result:
            return outcome, result
    return outcome, 0
