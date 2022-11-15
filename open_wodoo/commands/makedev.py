"""
Disconnects SMTP From odoo via Config.
Disconnects Label Printers via RPC.
Installs and configures Web Ribbon Banner.
Disables Audit rules via RPC.
"""

import logging
from configparser import ConfigParser

import typer
from wodoo_rpc.login import wait_for_odoo

from ..commands.rpc.cli import rpc_callback
from ..helpers.cli import typer_unpacker

LOGGER = logging.getLogger(__name__)


def remove_label_printers(odoo_api, printer_url="host.docker.internal"):
    """Set Odoo Label Printer URL.

    Parameters
    ----------
    odoo_api : _type_
        Logged in Odoo API
    printer_url : str, optional
        printer url, by default "host.docker.internal"
    """
    printer_env = odoo_api.session.env["wetech.label.printer"]
    l_printer_ids = printer_env.search([])
    l_printers = printer_env.browse(l_printer_ids)
    LOGGER.info("Setting Label Printer URL for %s Printers", len(l_printers))
    l_printers.write({"hostname": printer_url})


def staging_banner(odoo_api, banner_text: str = "", banner_background_color: str = ""):
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
    mod_ids = mod.search(["&", ("name", "in", ["web_environment_ribbon"]), ("state", "=", "uninstalled")])
    modules = mod.browse(mod_ids)
    if modules:
        LOGGER.info("Installing Modules: %s", ",".join(modules.mapped("name")))
        modules.button_immediate_install()
    param_env = odoo_api.session.env["ir.config_parameter"]
    ribbon_text_id = param_env.search([("key", "=", "ribbon.name")])
    param_env.browse(ribbon_text_id).value = banner_text
    LOGGER.info("Setting Banner Text to: %s", banner_text)
    ribbon_text_id = param_env.search([("key", "=", "ribbon.background.color")])
    param_env.browse(ribbon_text_id).value = banner_background_color
    LOGGER.info("Setting Banner background color: %s", banner_background_color)


def disable_audit_logs(odoo_api):
    """Disable audit log rules.

    Parameters
    ----------
    odoo_api : _type_
        Logged in Odoo API
    """
    mod = odoo_api.session.env["auditlog.rule"]
    rule_ids = mod.search([("state", "=", "subscribed")])
    rules = mod.browse(rule_ids)
    if rules:
        LOGGER.info("Disabling '%s' audit log rules", len(rules))
        rules.unsubscribe()


@typer_unpacker
def makedev_config(ctx: typer.Context):
    """
    Set odoo.conf values for Staging Env.
    """
    odoo_conf = ConfigParser()
    odoo_conf.read(ctx.obj.odoo_conf_path)
    LOGGER.info("Setting Odoo Conf Options for Stage")
    odoo_conf["options"].update(
        {
            "smtp_server": "localhost",
            "smtp_password": "False",
            "smtp_user": "False",
            "logfile": "",
            "limit_memory_hard": "2684354560",
            "limit_memory_soft": "2147483648",
            "workers": "3",
            "max_cron_threads": "1",
            "limit_time_cpu": "36000",
            "limit_time_real": "42000",
        }
    )

    with ctx.obj.odoo_conf_path.open("w") as conf:
        odoo_conf.write(conf)


@typer_unpacker
def makedev_rpc(
    ctx: typer.Context,
    banner_text: str = typer.Option(
        "Development", envvar="ODOO_BANNER_TEXT", help="Text to display in Top left ribbon banner"
    ),
    banner_background_color: str = typer.Option(
        "rgba(255,0,0,.6)", envvar="ODOO_BANNER_BG_COLOR", help="Top left ribbon banner color"
    ),
):
    """
    Set some Odoo Options via RPC to disable prod features.
    """

    LOGGER.info("Starting Stagesplitter")
    odoo_api = wait_for_odoo(
        odoo_host=ctx.obj.odoo_rpc_host,
        odoo_db=ctx.obj.odoo_main_db,
        odoo_user=ctx.obj.odoo_rpc_user,
        odoo_password=ctx.obj.odoo_rpc_password,
    )

    remove_label_printers(odoo_api=odoo_api)
    staging_banner(odoo_api=odoo_api, banner_text=banner_text, banner_background_color=banner_background_color)
    disable_audit_logs(odoo_api=odoo_api)


def makedec_cli_app():
    app = typer.Typer(callback=rpc_callback)
    app.command("rpc", help="Set odoo.conf values for Staging Env.")(makedev_rpc)
    app.command("config", help="Set some Odoo Options via RPC to disable prod features.")(makedev_config)
    return app
