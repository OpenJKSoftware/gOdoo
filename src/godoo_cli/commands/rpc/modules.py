import logging
from typing import Any, List

import typer
from godoo_rpc import OdooApiWrapper
from godoo_rpc.login import wait_for_odoo

from ...helpers.cli import typer_retuner
from .cli import rpc_callback

LOGGER = logging.getLogger(__name__)


def rpc_get_modules(odoo_api: OdooApiWrapper, module_query: str, valid_module_names: List[str] = None):
    """Get ir.module.module records by a query search string.

    Parameters
    ----------
    odoo_api : OdooApiWrapper
        Odoo Wrapper
    module_query : str
        Custom query. Module name. Comma sep list of modules or % wildcards are supported
    valid_module_names : List[str], optional
        only allow certain model names to be returned, by default None

    Returns
    -------
    _type_
        _description_
    """
    mod_env = odoo_api.session.env["ir.module.module"]
    mod_env.update_list()

    base_domain = []
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

    LOGGER.debug("Searching for Modules with Domain: %s", base_domain + search_domain)
    if ids := mod_env.search(base_domain + search_domain):
        modules = mod_env.browse(ids)
        LOGGER.debug("Found Modules: %s", [(m.id, m.name, m.state) for m in modules])
        return modules


def rpc_install_modules(
    modules: Any,
    upgrade: bool = True,
):
    """Install and upgrade Modules to Database.

    Parameters
    ----------
    modules : Iterable RPC ir.module.module records
        List of
    upgrade : bool, optional
        Upgrade module if already installed, by default True
    """
    did_something = False
    install_module_ids = [m.id for m in modules if m.state == "uninstalled"]
    if install_module_ids:
        install_modules = modules.browse(install_module_ids)
        LOGGER.info("Installing Module: " + ", ".join(install_modules.mapped("name")))
        install_modules.button_immediate_install()
        did_something = True

    if upgrade and (
        up_module_ids := [m.id for m in modules if m.state == "installed" and m.id not in install_module_ids]
    ):
        up_modules = modules.browse(up_module_ids)
        LOGGER.info("Updating Module: " + ", ".join(up_modules.mapped("name")))
        up_modules.button_immediate_upgrade()
        did_something = True
    return did_something


def install_modules(
    ctx: typer.Context,
    module_name_query: str = typer.Argument(..., help=r"Module Internal name. Will use ilike Match if \% is present"),
    upgrade: bool = typer.Option(True, help="Upgrae Module if already installed"),
):
    """Upgrades or Installs Modules in Odoo via RPC."""

    odoo_api = wait_for_odoo(
        odoo_host=ctx.obj.odoo_rpc_host,
        odoo_db=ctx.obj.odoo_main_db,
        odoo_user=ctx.obj.odoo_rpc_user,
        odoo_password=ctx.obj.odoo_rpc_password,
    )

    if modules := rpc_get_modules(odoo_api, module_name_query):
        if rpc_install_modules(modules):
            return
        else:
            LOGGER.warn("Found Modules, but didn't do anything on DB.")
    else:
        LOGGER.warn("Could not find modules with Query: '%s'", module_name_query)
    return typer_retuner(1)


def modules_cli_app():
    app = typer.Typer(
        callback=rpc_callback,
        no_args_is_help=True,
        help="Wrapper around Odoo modules. (Install/upgrade, etc)",
    )

    app.command(name="install")(install_modules)
    return app
