"""Module management functionality for Odoo via RPC.

This module provides tools for managing Odoo modules through Remote Procedure
Call (RPC) methods, including installation, uninstallation, and querying
module status.
"""

import logging
from typing import Annotated, Any, Optional

import typer
from godoo_rpc import OdooApiWrapper
from godoo_rpc.login import wait_for_odoo

from ...cli_common import CommonCLI

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def rpc_get_modules(
    odoo_api: OdooApiWrapper, module_query: str, valid_module_names: Optional[list[str]] = None
) -> list[Any]:
    """Get ir.module.module records by a query search string.

    This function searches for Odoo modules based on a query string and
    optional list of valid module names.

    Args:
        odoo_api: The Odoo API wrapper for RPC communication.
        module_query: A search string to query modules.
        valid_module_names: Optional list of valid module names to filter results.

    Returns:
        A list of module records matching the query.
    """
    mod_env = odoo_api.session.env["ir.module.module"]
    mod_env.update_list()

    base_domain = []
    if "," in module_query:
        search_domain = [("name", "in", module_query.split(","))]
    else:
        search_domain = [("name", "=ilike", module_query)] if "%" in module_query else [("name", "=", module_query)]

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
        LOGGER.info("Installing Module: " + ", ".join([m.name for m in install_modules]))
        install_modules.button_immediate_install()
        did_something = True

    if upgrade and (
        up_module_ids := [m.id for m in modules if m.state == "installed" and m.id not in install_module_ids]
    ):
        up_modules = modules.browse(up_module_ids)
        LOGGER.info("Updating Module: " + ", ".join([m.name for m in up_modules]))
        up_modules.button_immediate_upgrade()
        did_something = True
    return did_something


def install_modules(
    module_name_query: Annotated[
        str,
        typer.Argument(help=r"Module Internal name(s), comma seperated. Will use ilike Match if \% is present"),
    ],
    rpc_host: Annotated[str, CLI.rpc.rpc_host],
    rpc_database: Annotated[str, CLI.rpc.rpc_db_name],
    rpc_user: Annotated[str, CLI.rpc.rpc_user],
    rpc_password: Annotated[str, CLI.rpc.rpc_password],
    upgrade: Annotated[bool, typer.Option(help="Upgrae Module if already installed")] = True,
):
    """Install specified Odoo modules via RPC.

    This function allows installing one or more Odoo modules in a running
    Odoo instance. Optionally, it can update modules that are already
    installed.
    """
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


def uninstall_modules(
    module_name_query: Annotated[
        str,
        typer.Argument(help=r"Module Internal name(s), comma seperated. Will use ilike Match if \% is present"),
    ],
    rpc_host: Annotated[str, CLI.rpc.rpc_host],
    rpc_database: Annotated[str, CLI.rpc.rpc_db_name],
    rpc_user: Annotated[str, CLI.rpc.rpc_user],
    rpc_password: Annotated[str, CLI.rpc.rpc_password],
):
    """Uninstall specified Odoo modules via RPC.

    This function allows uninstalling one or more Odoo modules from a
    running Odoo instance.
    """
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
            LOGGER.info("Uninstalling Module: " + ", ".join([m.name for m in uninstall_modules]))
            uninstall_modules.button_immediate_uninstall()
        else:
            LOGGER.warn("Found Modules, but didn't do anything on DB.")
    else:
        LOGGER.warn("Could not find modules with Query: '%s'", module_name_query)
    return CLI.returner(1)
