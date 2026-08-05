"""Composable lifecycle orchestration for a gOdoo runtime."""

import logging
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
