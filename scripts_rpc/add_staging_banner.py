"""Install Web Ribbon addon and set Text and color"""
import logging
import os

from godoo_rpc import OdooApiWrapper

LOGGER = logging.getLogger(__name__)


def staging_banner(
    odoo_api: OdooApiWrapper, banner_text: str = "Development", banner_background_color: str = "rgba(255,0,0,.6)"
):
    """Set Odoo top left Banner.

    Parameters
    ----------
    odoo_api : _type_
        Logged in Odoo API
    banner_text : str, optional
        Text to display on banner, by default ""
    banner_background_color : str, optional
        rbg("","","",""), by default ""
    """
    mod = odoo_api.session.env["ir.module.module"]
    mod_ids = mod.search([("name", "in", ["web_environment_ribbon"])])
    if not mod_ids:
        LOGGER.warning("Cannot set Stage banner. Module: 'web_environment_ribbon' not available")
        return
    if module := mod.browse(mod_ids):
        if module.state == "uninstalled":
            LOGGER.info("Installing Modules: %s", ",".join(module.mapped("name")))
            module.button_immediate_install()

    param_env = odoo_api.session.env["ir.config_parameter"]
    ribbon_text_id = param_env.search([("key", "=", "ribbon.name")])
    param_env.browse(ribbon_text_id).value = banner_text
    LOGGER.info("Setting Banner Text to: %s", banner_text)
    ribbon_text_id = param_env.search([("key", "=", "ribbon.background.color")])
    param_env.browse(ribbon_text_id).value = banner_background_color
    LOGGER.info("Setting Banner background color: %s", banner_background_color)


def Main(odoo_api: OdooApiWrapper):
    staging_banner(
        odoo_api,
        os.getenv("ODOO_BANNER_TEXT"),
        os.getenv("ODOO_BANNER_BG_COLOR"),
    )
