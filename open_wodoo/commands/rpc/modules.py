import logging
from typing import List

import typer
from wodoo_rpc import OdooApiWrapper
from wodoo_rpc.login import wait_for_odoo

from ...helpers.cli import typer_retuner
from .cli import rpc_callback

LOGGER = logging.getLogger(__name__)


def rpc_get_modules(
    odoo_api: OdooApiWrapper, module_query: str, installed: bool = True, valid_module_names: List[str] = None
):
    mod = odoo_api.session.env["ir.module.module"]
    mod.update_list()

    base_domain = ["&", ("state", "=", "installed" if installed else "uninstalled")]
    if "," in module_query:
        search_domain = [("name", "in", module_query.split(","))]
    else:
        if "%" in module_query:
            search_domain = [("name", "=ilike", module_query)]
        else:
            search_domain = [("name", "=", module_query)]

    if valid_module_names:
        base_domain.insert(1, "&")
        base_domain.append(("name", "in", valid_module_names))

    module_ids = mod.search(base_domain + search_domain)
    if module_ids:
        return mod.browse(module_ids)


def install_modules(
    ctx: typer.Context,
    module_name_query: str = typer.Argument(..., help=r"Module Internal name. Will use ilike Match if \% is present"),
    install: bool = typer.Option(True, help="Install Module if not already installed"),
):
    """Upgrades or Installs Modules in Odoo via RPC."""

    odoo_api = wait_for_odoo(
        odoo_host=ctx.obj.odoo_rpc_host,
        odoo_db=ctx.obj.odoo_main_db,
        odoo_user=ctx.obj.odoo_rpc_user,
        odoo_password=ctx.obj.odoo_rpc_password,
    )
    mod = odoo_api.session.env["ir.module.module"]
    mod.update_list()
    did_something = False
    upgrade_modules = rpc_get_modules(odoo_api, module_name_query, installed=True)

    if install:
        install_modules = rpc_get_modules(odoo_api, module_name_query, installed=False)
        if install_modules:
            LOGGER.info("Installing Module: " + ", ".join(install_modules.mapped("name")))
            install_modules.button_immediate_install()
            did_something = True

    if upgrade_modules:
        LOGGER.info("Updating Module: " + ", ".join(upgrade_modules.mapped("name")))
        upgrade_modules.button_immediate_upgrade()
        did_something = True
    if not did_something:
        LOGGER.warn("Could not find modules with Query: '%s'", module_name_query)
        return typer_retuner(1)


def modules_cli_app():
    app = typer.Typer(callback=rpc_callback)

    app.command(name="install")(install_modules)
    return app
