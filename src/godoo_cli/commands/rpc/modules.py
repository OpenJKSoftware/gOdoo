import logging
from typing import Any, List

import typer
from godoo_rpc import OdooApiWrapper
from godoo_rpc.login import wait_for_odoo

from ...cli_common import CommonCLI

CLI = CommonCLI()
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


@CLI.arg_annotator
def install_modules(
    module_name_query: str = typer.Argument(
        ..., help=r"Module Internal name(s), comma seperated. Will use ilike Match if \% is present"
    ),
    rpc_host=CLI.rpc.rpc_host,
    rpc_database=CLI.rpc.rpc_db_name,
    rpc_user=CLI.rpc.rpc_user,
    rpc_password=CLI.rpc.rpc_password,
    upgrade: bool = typer.Option(True, help="Upgrae Module if already installed"),
):
    """Install or upgrade Odoo modules via RPC. Can act on multiple modules with % wildcard"""

    odoo_api = wait_for_odoo(
        odoo_host=rpc_host,
        odoo_db=rpc_database,
        odoo_user=rpc_user,
        odoo_password=rpc_password,
    )

    if modules := rpc_get_modules(odoo_api, module_name_query):
        if rpc_install_modules(modules, upgrade=upgrade):
            return
        else:
            LOGGER.warn("Found Modules, but didn't do anything on DB.")
    else:
        LOGGER.warn("Could not find modules with Query: '%s'", module_name_query)
    return CLI.returner(1)


@CLI.arg_annotator
def uninstall_modules(
    module_name_query: str = typer.Argument(
        ..., help=r"Module Internal name(s), comma seperated. Will use ilike Match if \% is present"
    ),
    rpc_host=CLI.rpc.rpc_host,
    rpc_database=CLI.rpc.rpc_db_name,
    rpc_user=CLI.rpc.rpc_user,
    rpc_password=CLI.rpc.rpc_password,
):
    """Uninstall odoo Modules via RPC. Can act on multiple modules with % wildcard"""

    odoo_api = wait_for_odoo(
        odoo_host=rpc_host,
        odoo_db=rpc_database,
        odoo_user=rpc_user,
        odoo_password=rpc_password,
    )

    if modules := rpc_get_modules(odoo_api, module_name_query):
        uninstall_module_ids = [m.id for m in modules if m.state == "installed"]
        if uninstall_module_ids:
            uninstall_modules = modules.browse(uninstall_module_ids)
            LOGGER.info("Uninstalling Module: " + ", ".join(uninstall_modules.mapped("name")))
            uninstall_modules.button_immediate_uninstall()
        else:
            LOGGER.warn("Found Modules, but didn't do anything on DB.")
    else:
        LOGGER.warn("Could not find modules with Query: '%s'", module_name_query)
    return CLI.returner(1)
