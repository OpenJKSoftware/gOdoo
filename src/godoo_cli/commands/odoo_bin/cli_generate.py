"""Methods to generate odoo-bin Commands as Strings."""

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from ...models import GodooConfig, GodooModules

LOGGER = logging.getLogger(__name__)


def _launch_command(
    godoo_conf: GodooConfig,
    extra_cmd_args: list[str],
    upgrade_workspace_modules: bool = True,
) -> str:
    """Build the Odoo launch command with all necessary arguments.

    This function constructs the command line string used to launch Odoo,
    including handling module upgrades and configuration paths.

    Args:
        godoo_conf: GodooConfig object with Odoo configuration details.
        extra_cmd_args: Additional command line arguments to pass to odoo-bin.
        upgrade_workspace_modules: If True, automatically upgrade all modules in workspace.

    Returns:
        str: The complete command string to launch Odoo.
    """
    upgrade_addons = []
    if not any(arg in ("-u", "--update") for arg in extra_cmd_args) or upgrade_workspace_modules:
        all_modules = GodooModules(godoo_conf.workspace_addon_path).get_modules()
        upgrade_addons = [
            module.name for module in all_modules if module.version.split(".")[0] == godoo_conf.odoo_version.major
        ]

    update_addon_string = "--update " + ",".join(upgrade_addons) if upgrade_addons else ""

    odoo_cmd = [
        str(godoo_conf.odoo_bin_path.absolute()),
        update_addon_string,
        f"-c {godoo_conf.odoo_conf_path.absolute()!s}",
        *extra_cmd_args,
    ]
    odoo_cmd = list(filter(None, odoo_cmd))
    return " ".join(odoo_cmd)


def _add_default_argument(cmd_list: list[str], arg: str, arg_val: Any):
    """Add a default argument to the command list if not already present.

    Args:
        cmd_list: List of command arguments.
        arg: Argument name to add.
        arg_val: Value for the argument.
    """
    if not any(arg in s for s in cmd_list):
        cmd_list.append(f'{arg}="{arg_val}"')


def _boostrap_command(
    godoo_config: GodooConfig,
    addon_paths: list[Path],
    extra_cmd_args: Optional[list[str]] = None,
    install_workspace_modules: bool = True,
) -> str:
    """Generate bootstrap command for Odoo initialization.

    This function constructs the Odoo bootstrap command with all necessary parameters
    including database configuration, addon paths, and worker settings.

    Args:
        godoo_config: GodooConfig object with Odoo configuration details.
        addon_paths: List of paths for odoo-bin --addons-path.
        workspace_addon_path: Path to addons in dev repo.
        extra_cmd_args: Extra args to pass to odoo-bin.
        install_workspace_modules: Whether to install all modules found in workspace_path.

    Returns:
        The complete odoo-bin command string.
    """
    LOGGER.info("Generating Bootstrap Command")

    godoo_config.odoo_conf_path.parent.mkdir(parents=True, exist_ok=True)

    db_command = [
        f"--database {godoo_config.db_name}",
        f"--db_user {godoo_config.db_user}",
        f"--db_password {godoo_config.db_password}",
        f"--db_host {godoo_config.db_host}" if godoo_config.db_host else "",
        f"--db_port {godoo_config.db_port}" if godoo_config.db_port else "",
        f"--db-filter=^{godoo_config.db_filter}$",
    ]

    LOGGER.info("Getting Addon Paths")

    init_modules = []

    extra_cmd_args_str = " ".join(extra_cmd_args or [])
    if not re.search(r"(-i|--init) ", extra_cmd_args_str):
        if install_workspace_modules:
            workspace_modules = GodooModules([godoo_config.workspace_addon_path])
            if workspace_addons := workspace_modules.get_modules():
                init_modules = [
                    f.name for f in workspace_addons if f.version.split(".")[0] == godoo_config.odoo_version.major
                ]
        init_modules = init_modules or ["base", "web"]
    init_cmd = "--init " + ",".join(init_modules) if init_modules else ""

    addon_paths_str_list = [str(p.absolute()) for p in addon_paths if p and p.exists()]
    addon_paths_str = ", ".join(addon_paths_str_list)

    base_cmds = [
        str(godoo_config.odoo_bin_path.absolute()),
        init_cmd,
        f"--config {godoo_config.odoo_conf_path.absolute()!s}",
        "--save",
        f"--load-language {godoo_config.languages}",
        "--stop-after-init",
        f"--addons-path '{addon_paths_str}'",
    ]
    odoo_cmd = base_cmds + db_command
    if extra_cmd_args:
        odoo_cmd += extra_cmd_args

    _add_default_argument(cmd_list=odoo_cmd, arg="--data-dir", arg_val="/var/lib/odoo")

    if godoo_config.multithread_worker_count == -1:
        godoo_config.multithread_worker_count = int(os.cpu_count() or 2 / 2)

    if godoo_config.multithread_worker_count > 0:
        odoo_cmd += [
            "--proxy-mode",
            f"--workers {int(godoo_config.multithread_worker_count)}",
        ]

    odoo_cmd = list(filter(None, odoo_cmd))
    cmd_str = " ".join(odoo_cmd)
    return cmd_str
