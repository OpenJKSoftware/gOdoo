"""Methods to generate argv lists for ``odoo-bin`` invocations."""

import logging
import os
import shlex
from pathlib import Path
from typing import Optional

from ...models import GodooConfig, GodooModules
from ..db.query import DbBootstrapStatus, _is_bootstrapped

LOGGER = logging.getLogger(__name__)


def _odoo_config_args(godoo_config: GodooConfig, save: bool) -> list[str]:
    """Build Odoo config and database arguments."""
    config_args = [
        "--config",
        str(godoo_config.odoo_conf_path.absolute()),
        "--data-dir",
        str(godoo_config.data_dir.absolute()),
    ]
    if not save:
        return config_args

    godoo_config.odoo_conf_path.parent.mkdir(parents=True, exist_ok=True)
    return [
        *config_args,
        "--save",
        "--database",
        godoo_config.db_name,
        "--db_user",
        godoo_config.db_user,
        "--db_password",
        godoo_config.db_password,
        *(["--db_host", godoo_config.db_host] if godoo_config.db_host else []),
        *(["--db_port", str(godoo_config.db_port)] if godoo_config.db_port else []),
        f"--db-filter=^{godoo_config.db_filter}$",
    ]


def _extra_args_argv(extra_cmd_args: list[str]) -> list[str]:
    """Normalize legacy option chunks while preserving already-separated values.

    Historically Typer supplied option chunks such as ``"--update sale"``.
    Internal callers may now also provide canonical argv pairs, where a value
    (including one with spaces) follows its option as a separate list item.
    """
    argv: list[str] = []
    for chunk in extra_cmd_args:
        argv.extend(shlex.split(chunk) if chunk.startswith("-") else [chunk])
    return argv


def _launch_command(
    godoo_conf: GodooConfig,
    extra_cmd_args: list[str],
    upgrade_workspace_modules: bool = True,
) -> list[str]:
    """Build an Odoo launch argument vector."""
    extra_args = _extra_args_argv(extra_cmd_args)
    has_explicit_update = any(
        arg == option or arg.startswith(f"{option}=") for arg in extra_args for option in ("-u", "--update")
    )
    upgrade_addons = []
    if upgrade_workspace_modules and not has_explicit_update:
        all_modules = GodooModules(godoo_conf.workspace_addon_path).get_modules()
        upgrade_addons = [
            module.name for module in all_modules if module.version.split(".")[0] == godoo_conf.odoo_version.major
        ]

    update_args = ["--update", ",".join(upgrade_addons)] if upgrade_addons else []
    config_args = _odoo_config_args(godoo_conf, save=not godoo_conf.odoo_conf_path.exists())
    return [
        str(godoo_conf.odoo_bin_path.absolute()),
        *update_args,
        *config_args,
        *extra_args,
    ]


def _boostrap_command(
    godoo_config: GodooConfig,
    addon_paths: list[Path],
    extra_cmd_args: Optional[list[str]] = None,
    install_workspace_modules: bool = True,
) -> list[str]:
    """Generate an argv vector for Odoo initialization."""
    LOGGER.info("Generating Bootstrap Command")
    extra_args = _extra_args_argv(extra_cmd_args or [])
    has_module_action = any(
        arg in ("-i", "--init", "-u", "--update") or arg.startswith(("--init=", "--update=")) for arg in extra_args
    )

    init_modules: list[str] = []
    if install_workspace_modules and not has_module_action:
        LOGGER.debug("Auto-detecting workspace modules for Bootstrapping")
        workspace_modules = GodooModules([godoo_config.workspace_addon_path])
        if workspace_addons := workspace_modules.get_modules():
            init_modules = [
                module.name
                for module in workspace_addons
                if module.version.split(".")[0] == godoo_config.odoo_version.major
            ]
        init_modules = init_modules or ["base", "web"]

    init_args: list[str] = []
    if init_modules:
        action = (
            "--update" if _is_bootstrapped(godoo_config.db_connection) == DbBootstrapStatus.BOOTSTRAPPED else "--init"
        )
        init_args = [action, ",".join(init_modules)]

    addon_paths_arg = ",".join(str(path.absolute()) for path in addon_paths if path and path.exists())
    odoo_cmd = [
        str(godoo_config.odoo_bin_path.absolute()),
        *init_args,
        *_odoo_config_args(godoo_config, save=True),
        "--load-language",
        godoo_config.languages,
        "--stop-after-init",
        "--addons-path",
        addon_paths_arg,
        *extra_args,
    ]

    worker_count = godoo_config.multithread_worker_count
    if worker_count == -1:
        worker_count = int((os.cpu_count() or 2) / 2)
    if worker_count > 0:
        odoo_cmd.extend(["--proxy-mode", "--workers", str(worker_count)])
    return odoo_cmd
