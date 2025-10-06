"""Module for bootstrapping Odoo instances.

This module provides functionality to bootstrap Odoo instances by installing
required Python dependencies, setting up database connections, and initializing
the Odoo environment with necessary modules and configurations.
"""

import logging
import os
import re
from pathlib import Path
from typing import Annotated, Optional, Union

import typer

from ...cli_common import CommonCLI
from ...helpers.modules_py import _install_py_reqs_by_odoo_cmd
from ...helpers.system import run_cmd
from ...models import GodooConfig
from ..db.query import DbBootstrapStatus, _is_bootstrapped
from ..source_get import py_depends_by_db, update_odoo_conf
from .cli_generate import _boostrap_command, _launch_command
from .shell import odoo_shell_run_script

CLI = CommonCLI()

LOGGER = logging.getLogger(__name__)


def bootstrap_and_prep_launch_cmd(  # noqa: C901
    godoo_conf: GodooConfig,
    odoo_demo: bool,
    dev_mode: bool,
    extra_launch_args: Optional[list[str]] = None,
    extra_bootstrap_args: Optional[list[str]] = None,
    log_file_path: Optional[Path] = None,
    install_workspace_addons: bool = True,
    launch_or_bootstrap: bool = False,
) -> Union[int, str]:
    """Bootstrap an Odoo instance if needed and prepare the launch command.

    This function handles the complete process of preparing an Odoo instance for launch:
    1. Checks if the database exists and is bootstrapped
    2. Bootstraps the database if needed
    3. Installs Python dependencies
    4. Updates odoo.conf with current addon paths
    5. Prepares the launch command with appropriate options

    Args:
        godoo_conf: GodooConfig object with Odoo configuration details.
        odoo_demo: If True, load demo data during bootstrap.
        dev_mode: If True, enable development mode features.
        extra_launch_args: Additional arguments for the launch command.
        extra_bootstrap_args: Additional arguments for the bootstrap process.
        log_file_path: Path to the log file (None for stdout).
        install_workspace_addons: If True, install all modules found in workspace.
        launch_or_bootstrap: If True, only return launch command if bootstrap didn't run.

    Returns:
        Union[int, str]: Either a non-zero error code if bootstrap failed,
            or the launch command string if successful.
    """
    LOGGER.info("Starting godoo Init Script")

    extra_odoo_args = []
    if log_file_path is not None:
        log_file_path.unlink(missing_ok=True)
        extra_odoo_args.append("--logfile " + str(log_file_path.absolute()))

    bootstraped = _is_bootstrapped(godoo_conf.db_connection)
    LOGGER.info("Bootstrap Flag Status: '%s'", bootstraped.value)
    ret = ""
    did_bootstrap = False
    if bootstraped != DbBootstrapStatus.BOOTSTRAPPED:
        _extra_bootstrap_args = extra_odoo_args.copy()
        if ea := extra_bootstrap_args:
            _extra_bootstrap_args += ea
        if not odoo_demo:
            _extra_bootstrap_args += ["--without-demo all"]
        ret = bootstrap_odoo(
            **godoo_conf.db_connection.cli_dict,
            db_filter=godoo_conf.db_filter,
            odoo_main_path=godoo_conf.odoo_install_folder,
            workspace_addon_path=godoo_conf.workspace_addon_path,
            thirdparty_addon_path=godoo_conf.thirdparty_addon_path,
            odoo_conf_path=godoo_conf.odoo_conf_path,
            extra_cmd_args=_extra_bootstrap_args,
            install_workspace_modules=install_workspace_addons,
            multithread_worker_count=godoo_conf.multithread_worker_count,
            languages=godoo_conf.languages,
        )
        bootstraped = ret == 0
        if not bootstraped or launch_or_bootstrap:
            return ret
        did_bootstrap = True
        install_workspace_addons = False

    if ea := extra_launch_args:
        extra_odoo_args += ea

    odoo_version = godoo_conf.odoo_version

    if (
        bootstraped != DbBootstrapStatus.BOOTSTRAPPED
        and _is_bootstrapped(godoo_conf.db_connection) != DbBootstrapStatus.BOOTSTRAPPED
    ):
        LOGGER.error("404 Database not found. Aborting Launch...")
        return 404

    update_odoo_conf(
        odoo_conf=godoo_conf.odoo_conf_path,
        odoo_main_path=godoo_conf.odoo_install_folder,
        workspace_addon_path=godoo_conf.workspace_addon_path,
        thirdparty_addon_path=godoo_conf.thirdparty_addon_path,
    )
    if not did_bootstrap:
        py_depends_by_db(
            odoo_main_path=godoo_conf.odoo_install_folder,
            workspace_addon_path=godoo_conf.workspace_addon_path,
            thirdparty_addon_path=godoo_conf.thirdparty_addon_path,
            **godoo_conf.db_connection.cli_dict,
        )

    if dev_mode:
        extra_odoo_args.append("--dev xml,qweb,reload")
        if odoo_version.major == 16:
            extra_odoo_args[-1] += ",werkzeug"

    if godoo_conf.multithread_worker_count == 0:
        extra_odoo_args.append("--workers 0")

    return _launch_command(
        godoo_conf=godoo_conf,
        extra_cmd_args=extra_odoo_args,
        upgrade_workspace_modules=install_workspace_addons,
    )


def bootstrap_odoo(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_filter: Annotated[str, CLI.database.db_filter],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host],
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    extra_cmd_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
    multithread_worker_count: Annotated[int, CLI.odoo_launch.multithread_worker_count] = 2,
    languages: Annotated[str, CLI.odoo_launch.languages] = "de_DE,en_US",
    install_workspace_modules: Annotated[bool, CLI.odoo_launch.install_workspace_modules] = True,
    install_base_modules: Annotated[
        bool,
        typer.Option(
            help="Install base and web modules if no other -i or -u is present in Bootstrap",
            rich_help_panel="Odoo",
        ),
    ] = True,
    banner_text: Annotated[str, CLI.odoo_launch.banner_text] = "",
    banner_bg_color: Annotated[str, CLI.odoo_launch.banner_bg_color] = "green",
):
    """Bootstrap an Odoo instance with specified configuration."""
    godoo_conf = GodooConfig(
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_filter=db_filter,
        odoo_install_folder=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        multithread_worker_count=multithread_worker_count,
        languages=languages,
    )

    addon_paths = godoo_conf.addon_paths
    cmd_string = _boostrap_command(
        godoo_config=godoo_conf,
        addon_paths=addon_paths,
        extra_cmd_args=extra_cmd_args,
        install_workspace_modules=install_workspace_modules,
    )
    if not install_base_modules:
        cmd_string = re.sub(r"--init base,web", "", cmd_string)

    # Always update Pip reqs regardless of --no-update-source
    _install_py_reqs_by_odoo_cmd(addon_paths=addon_paths, odoo_bin_cmd=cmd_string)

    LOGGER.info("Launching Bootstrap Commandline")
    ret = run_cmd(cmd_string).returncode
    if ret != 0:
        LOGGER.error("Odoo-Bin Returned %d", ret)
        return CLI.returner(ret)
    if banner_text:
        os.environ["ODOO_BANNER_TEXT"] = banner_text
        os.environ["ODOO_BANNER_BG_COLOR"] = banner_bg_color
        odoo_shell_run_script(
            script_name="odoo_banner",
            odoo_main_path=odoo_main_path,
            odoo_conf_path=odoo_conf_path,
            db_name=db_name,
            db_user=db_user,
            db_host=db_host,
            db_port=db_port,
            db_password=db_password,
        )
    return CLI.returner(ret)
