"""Odoo shell interaction module.

This module provides functionality for interacting with the Odoo shell,
including running Python scripts and executing shell commands in the
Odoo environment. It supports both interactive and script-based operations.
"""

import logging
import os
from pathlib import Path
from typing import List

import typer

from ...cli_common import CommonCLI
from ...helpers.odoo_files import odoo_bin_get_version
from ...helpers.system import run_cmd

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)


@CLI.unpacker
@CLI.arg_annotator
def uninstall_modules(
    module_list: List[str] = typer.Argument(..., help="List of Modules to uninstall"),
    odoo_main_path=CLI.odoo_paths.bin_path,
    odoo_conf_path=CLI.odoo_paths.conf_path,
):
    """Uninstall specified modules from Odoo via shell.

    This function uses the Odoo shell to uninstall modules that are currently
    installed or in a state that allows uninstallation.

    Returns:
        int: 0 for success, non-zero for failure.
    """
    module_list_str = str(list(module_list))
    uninstall_cmd = f"env['ir.module.module'].search([('name','in',{module_list_str})]).filtered(lambda m: m.state not in ['uninstallable','uninstalled']).button_immediate_uninstall()"
    uninstall_shell = f'echo "{uninstall_cmd}" | {str(odoo_main_path.absolute())}/odoo-bin shell -c {str(odoo_conf_path.absolute())} --no-http'
    LOGGER.info("Launching Uninstaller: '%s'", uninstall_shell)
    ret = run_cmd(uninstall_shell).returncode
    return CLI.returner(ret)


@CLI.unpacker
@CLI.arg_annotator
def odoo_shell(
    pipe_in_command: str = typer.Argument("", help="Python command, that will be piped into odoo-bin shell"),
    odoo_main_path=CLI.odoo_paths.bin_path,
    odoo_conf_path=CLI.odoo_paths.conf_path,
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
):
    """Start an interactive Odoo shell session.

    This function launches an Odoo shell session, either using configuration from
    odoo.conf or direct database connection parameters. It supports both interactive
    mode and command piping.

    Returns:
        int: 0 for success, non-zero for failure.
    """
    shell_cmd = f"{str(odoo_main_path.absolute())}/odoo-bin shell --no-http"
    if odoo_conf_path.exists():
        shell_cmd += f" -c {str(odoo_conf_path.absolute())}"
    else:
        LOGGER.warning("No Odoo Config File found at %s", odoo_conf_path)
        if not all([db_host, db_port, db_name, db_user, db_password]):
            LOGGER.error("Missing database options and Config File. Aborting.")
            return CLI.returner(1)
        shell_cmd += f" --db_host={db_host} --db_port={db_port} --database={db_name} --db_user={db_user} --db_password={db_password}"

    if pipe_in_command:
        pipe_in_command = pipe_in_command.replace('"', '\\"')  # Escape Double Quotes
        shell_cmd = f'echo "{pipe_in_command}" |' + shell_cmd
        ret = run_cmd(shell_cmd)
    else:
        ret = run_cmd(shell_cmd, stdin=os.stdin)
    return CLI.returner(ret.returncode)


def complete_script_name():
    """Get a list of available script names for autocompletion.

    Returns:
        List[str]: List of script names without their .py extension.
    """
    script_folder = Path(__file__).parent / "scripts"
    return [p.stem for p in script_folder.glob("*.py")]


@CLI.unpacker
@CLI.arg_annotator
def odoo_shell_run_script(
    script_name: str = typer.Argument(..., help="Internal Script to run", autocompletion=complete_script_name),
    odoo_main_path=CLI.odoo_paths.bin_path,
    odoo_conf_path=CLI.odoo_paths.conf_path,
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
):
    """Run a predefined script using the Odoo shell.

    This function executes a Python script in the Odoo shell environment,
    supporting both configuration file and direct database connection parameters.

    Returns:
        int: 0 for success, non-zero for failure.
    """
    script_folder = Path(__file__).parent / "scripts"
    script_path = script_folder / f"{script_name}.py"
    if not script_path.exists():
        LOGGER.error("Script '%s' not found in %s", script_name, script_folder)
        return CLI.returner(1)

    shell_cmd = f"{str(odoo_main_path.absolute())}/odoo-bin shell --no-http"
    if odoo_conf_path.exists():
        shell_cmd += f" -c {str(odoo_conf_path.absolute())}"
    else:
        LOGGER.warning("No Odoo Config File found at %s", odoo_conf_path)
        if not all([db_host, db_port, db_name, db_user, db_password]):
            LOGGER.error("Missing database options and Config File. Aborting.")
            return CLI.returner(1)
        shell_cmd += f" --db_host={db_host} --db_port={db_port} --database={db_name} --db_user={db_user} --db_password={db_password}"

    LOGGER.info("Running Script: %s", script_path)
    run_cmd(shell_cmd, stdin=script_path.open("r"))


def odoo_pregenerate_assets(odoo_main_path: Path):
    """Use Odoo shell to pregenerate asset bundles.

    This function ensures that asset bundles are present in the filestore
    by pregenerating them through the Odoo shell.

    Raises:
        NotImplementedError: If the Odoo version is not supported.
    """
    odoo_version = odoo_bin_get_version(odoo_main_repo_path=odoo_main_path)
    if odoo_version.major == 16:
        pregen_command = "env['ir.qweb']._pregenerate_assets_bundles();env.cr.commit()"
    else:
        raise NotImplementedError(f"Odoo Version {odoo_version.raw} not supported in gOdoo for pregenerate_assets")
    LOGGER.info("Pregenerating Assets for Odoo version %s", odoo_version.raw)
    odoo_shell(pipe_in_command=pregen_command)
