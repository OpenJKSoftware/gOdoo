import logging

import typer
from godoo_rpc.login import wait_for_odoo

from ...cli_common import CommonCLI

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)


@CLI.arg_annotator
def set_config_parameter(
    param_name: str = typer.Argument(..., help=r"name of ir.config.parameter"),
    param_value: str = typer.Argument(..., help="Value to set config parameter to (:unlink to delete the parameter)"),
    rpc_host=CLI.rpc.rpc_host,
    rpc_database=CLI.rpc.rpc_db_name,
    rpc_user=CLI.rpc.rpc_user,
    rpc_password=CLI.rpc.rpc_password,
):
    """Create/Set/Delete ir.config_parameter in Odoo"""

    odoo_api = wait_for_odoo(
        odoo_host=rpc_host,
        odoo_db=rpc_database,
        odoo_user=rpc_user,
        odoo_password=rpc_password,
    )

    paramteter_model = odoo_api.session.env["ir.config_parameter"]

    param_id = paramteter_model.search([("key", "=", param_name)])
    param = paramteter_model.browse(param_id[0]) if param_id else None

    if param_value == ":unlink":
        if param:
            LOGGER.warning("Removing Config Parameter: %s", param_name)
            param.unlink()
        else:
            LOGGER.info("Param '%s' does not exist", param_name)
        return

    if param:
        LOGGER.info("Setting param value for %s", param_name)
        param.value = param_value
    else:
        LOGGER.info("Creating new Config Parameter: %s", param_name)
        paramteter_model.create({"key": param_name, "value": param_value})
