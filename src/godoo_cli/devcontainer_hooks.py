"""Explicit post-bootstrap behavior for the gOdoo DevContainer profile."""

import logging
from collections.abc import Callable
from pathlib import Path

LOGGER = logging.getLogger(__name__)

HookAction = Callable[[], int]
MigrationRunner = Callable[[Path], int]


def _run(action_name: str, action: HookAction) -> int:
    """Run one named hook action and preserve its process-style result."""
    result = action()
    if result:
        LOGGER.error("DevContainer post-bootstrap hook failed: %s", action_name)
    return result


def run_devcontainer_post_bootstrap_hooks(
    *,
    staging: bool,
    set_dev_password: bool,
    migrations_dir: Path,
    set_report_url: HookAction,
    set_all_user_passwords: HookAction,
    set_admin_login: HookAction,
    run_migration: MigrationRunner,
) -> int:
    """Run explicit DevContainer-only hooks after a newly created database.

    The hook runner deliberately receives actions instead of database or shell
    dependencies. Lifecycle orchestration decides *when* hooks are applicable;
    this profile module defines only the project-specific work to perform.
    """
    if result := _run("set report.url", set_report_url):
        return result
    if not staging:
        return 0

    if set_dev_password:
        if result := _run("set development passwords", set_all_user_passwords):
            return result
        if result := _run("set administrator login", set_admin_login):
            return result

    if not migrations_dir.is_dir():
        LOGGER.info("No staging migration directory at %s", migrations_dir)
        return 0
    for migration in sorted(migrations_dir.glob("*.py")):
        LOGGER.info("Running DevContainer post-bootstrap migration: %s", migration)
        if result := run_migration(migration):
            LOGGER.error("DevContainer migration failed: %s", migration)
            return result
    return 0
