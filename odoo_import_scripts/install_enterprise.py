"""Installs Odoo Enterprise. Can be Passed to wodoo load --load-data-path"""
import logging

from wodoo_rpc import OdooApiWrapper

from open_wodoo.commands.rpc.modules import rpc_install_modules

LOGGER = logging.getLogger(__name__)


def Main(odoo_api: OdooApiWrapper):
    mod_env = odoo_api.session.env["ir.module.module"]
    mod_env.update_list()

    module_ids = mod_env.search([("name", "in", ["web_enterprise"])])
    modules = mod_env.browse(module_ids)

    if not modules:
        LOGGER.warn("Could not find enterprise Modules")
        return

    if not rpc_install_modules(modules):
        LOGGER.warn("Found Modules, but didn't do anything on DB.")
