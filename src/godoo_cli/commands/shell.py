import logging
from typing import List

import typer

from ..helpers.cli import typer_retuner, typer_unpacker
from ..helpers.system import run_cmd

LOGGER = logging.getLogger(__name__)


@typer_unpacker
def uninstall_modules(
    ctx: typer.Context,
    module_list: List[str] = typer.Argument(..., help="List of Modules to uninstall"),
):
    """
    Uninstall given Modules from Odoo via shell.
    """
    module_list_str = str(list(module_list))
    uninstall_cmd = f"env['ir.module.module'].search([('name','in',{module_list_str})]).filtered(lambda m: m.state not in ['uninstallable','uninstalled']).button_immediate_uninstall()"
    uninstall_shell = f'echo "{uninstall_cmd}" | {str(ctx.obj.odoo_main_path.absolute())}/odoo-bin shell -c {str(ctx.obj.odoo_conf_path.absolute())} --no-http'
    LOGGER.info("Launching Uninstaller: '%s'", uninstall_shell)
    ret = run_cmd(uninstall_shell).returncode
    return typer_retuner(ret)


@typer_unpacker
def odoo_shell(
    ctx: typer.Context,
    pipe_in_command: str = typer.Argument(""),
):
    """
    Start Odoo session in an Interactive shell.
    """
    shell_cmd = (
        f"{str(ctx.obj.odoo_main_path.absolute())}/odoo-bin shell -c {str(ctx.obj.odoo_conf_path.absolute())} --no-http"
    )
    if pipe_in_command:
        shell_cmd = f'echo "{pipe_in_command}" |' + shell_cmd
    LOGGER.debug("Running Command: %s", shell_cmd)
    return typer_retuner(run_cmd(shell_cmd).returncode)
