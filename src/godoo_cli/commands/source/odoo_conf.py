"""Utilities for mutating Odoo config files."""

import configparser
import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def update_odoo_conf_addon_paths(odoo_conf: Path, addon_paths: list[Path]) -> None:
    """Update odoo.conf addons_path with the provided addon folders."""
    if not odoo_conf.exists():
        msg = f"Odoo.conf not found at: {odoo_conf!s}"
        LOGGER.error(msg)
        raise FileNotFoundError(msg)

    config = configparser.ConfigParser()
    config.read(odoo_conf)
    path_strings = [str(path.absolute()) for path in addon_paths]
    addon_path_option = ",".join(path_strings)
    config["options"]["addons_path"] = addon_path_option
    LOGGER.info("Writing Addon Paths to Odoo Config.")
    LOGGER.debug(addon_path_option)
    with odoo_conf.open("w") as config_file:
        config.write(config_file)
