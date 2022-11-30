"""
Set odoo.conf variables
"""
import logging
from configparser import ConfigParser
from pathlib import Path
from typing import List

import typer

from ..helpers.cli import typer_unpacker

LOGGER = logging.getLogger(__name__)


@typer_unpacker
def set_odoo_config(
    ctx: typer.Context,
    options: List[str] = typer.Argument(..., help="odoo.conf options by key=value"),
):
    """
    Set odoo.conf values.
    """
    conf_path = Path(ctx.obj.odoo_conf_path)
    odoo_conf = ConfigParser()
    odoo_conf.read(conf_path)
    LOGGER.info("Setting Odoo Conf Options for Stage")
    custom_opts = {}
    for op in options:
        if "=" not in op:
            LOGGER.error("Cannot parse Option: '%s' --> missing = sign", op)
            raise ValueError("")
        op_split = op.split("=")
        custom_opts[op_split[0]] = op_split[1]
    LOGGER.debug("Writing Conf Options: %s", custom_opts)
    if "options" not in odoo_conf:
        odoo_conf.add_section("options")
        odoo_conf["options"] = {}
    odoo_conf["options"].update(custom_opts)
    conf_path.touch(exist_ok=True)
    odoo_conf.write(conf_path.open("w"))
