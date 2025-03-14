"""Configuration management module for Odoo.

This module provides functionality to manage Odoo configuration files,
particularly odoo.conf. It allows setting configuration options through
command-line arguments.
"""

import logging
from configparser import ConfigParser
from pathlib import Path
from typing import Annotated

import typer

from ..cli_common import CommonCLI

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)


def set_odoo_config(
    options: Annotated[list[str], typer.Argument(help="odoo.conf options by key=value")],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
):
    """Set odoo.conf values."""
    conf_path = Path(odoo_conf_path)
    odoo_conf = ConfigParser()
    odoo_conf.read(conf_path)
    LOGGER.info("Setting Odoo Conf Options for Stage")
    custom_opts = {}
    for op in options:
        if "=" not in op:
            msg = f"Cannot parse Option: '{op}' --> missing '=' sign (key=value)"
            LOGGER.error(msg)
            raise ValueError(msg)
        op_split = op.split("=")
        custom_opts[op_split[0]] = op_split[1]
    LOGGER.debug("Writing Conf Options: %s", custom_opts)
    if "options" not in odoo_conf:
        odoo_conf.add_section("options")
        odoo_conf["options"] = {}
    odoo_conf["options"].update(custom_opts)
    conf_path.touch(exist_ok=True)
    odoo_conf.write(conf_path.open("w"))
