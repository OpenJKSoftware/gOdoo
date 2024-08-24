"""Install Web Ribbon addon and set Text and color. To be Piped into Odoo Shell."""

import logging
import os
from typing import Optional

from odoo import api

LOGGER = logging.getLogger(__name__)

env: api.Environment = env  # Just to silence pyright # pyright: ignore # NOQA


def set_banner(banner_text: Optional[str] = "Development", banner_background_color: Optional[str] = ""):
    """Set Odoo top left Banner.

    Parameters
    ----------
    banner_text : str, optional
        Text to display on banner, by default ""
    banner_background_color : str, optional
        rbg("","","",""), by default ""
    """
    if not banner_background_color:
        banner_background_color = "rgb(255,0,0)"

    view_env = env["ir.ui.view"]
    view_dic = {
        "name": "godoo.dev_banner",
        "type": "qweb",
        "mode": "extension",
        "active": True,
        "inherit_id": env.ref("web.layout").id,
        "arch_db": f"""
            <data name="Godoo Dev Banner" inherit_id="web.layout" active="False">
                <xpath expr="//body" position="inside">
                    <div>
                        <span id="oe_neutralize_banner" style="text-align: center; color: #FFFFFF; background-color: {banner_background_color}; position: relative; display: block; font-size: 16px;">
                            {banner_text}
                        </span>
                    </div>
                </xpath>
            </data>
        """,
    }
    if view := view_env.search([("name", "=", view_dic["name"])]):
        LOGGER.info("Updating Banner view: %s", view_dic["name"])
        view.update(view_dic)
    else:
        LOGGER.info(
            "Creating Banner view: %s\nText: %s\nColor: %s", view_dic["name"], banner_text, banner_background_color
        )
        view_env.create(view_dic)


def disable_record(ref):
    if rec := env.ref(ref, raise_if_not_found=False):
        if rec.active:
            LOGGER.info("Disabling Record: %s", rec.display_name)
            rec.active = False


def remove_upgrade_test_ribbon():
    disable_record("__upgrade__.upg_test_banner")
    disable_record("__upgrade__.upg_test_ribbon")
    disable_record("web.neutralize_banner")


set_banner(
    os.getenv("ODOO_BANNER_TEXT"),
    os.getenv("ODOO_BANNER_BG_COLOR"),
)
remove_upgrade_test_ribbon()
if ribb := env["ir.module.module"].search([("name", "=", "web_environment_ribbon"), ("state", "=", "installed")]):
    LOGGER.info("Uninstalling Web Environment Ribbon addon")
    ribb.button_immediate_uninstall()


env.cr.commit()
