"""Functions that operate on Odoos Source Code."""

import logging
import re
from pathlib import Path

import typer
from packaging.specifiers import SpecifierSet

from ..models import OdooVersion
from .system import run_cmd

LOGGER = logging.getLogger(__name__)


def odoo_bin_get_version(odoo_main_repo_path: Path) -> OdooVersion:
    """Get Odoo Version by calling 'odoo-bin --version'.

    Parameters
    ----------
    odoo_main_repo_path : Path
        Path to odoo-bin folder

    Returns:
    -------
    OdooVersion
        odoo-bin --version output parsed into Dataclass
    """
    odoo_bin_path = odoo_main_repo_path / "odoo-bin"
    version_out = run_cmd(f"{odoo_bin_path.absolute()} --version", capture_output=True, text=True)
    vers_match = re.match(r"(?P<text>.*) (?P<major>\d+)\.(?P<minor>\d+)", version_out.stdout)
    if vers_match:
        return OdooVersion(
            text=vers_match.group("text"),
            major=int(vers_match.group("major")),
            minor=int(vers_match.group("minor")),
        )
    msg = f"Could not parse Odoo Version from: '{version_out}'"
    LOGGER.error(msg)
    raise ValueError(msg)


def require_odoo_version(
    odoo_main_repo_path: Path,
    version_specifier: str,
) -> OdooVersion:
    """Return the Odoo version or reject one outside a semantic version specifier."""
    try:
        version = odoo_bin_get_version(odoo_main_repo_path)
    except ValueError as error:
        msg = f"Could not verify the Odoo runtime at {odoo_main_repo_path}: {error}"
        LOGGER.exception(msg)
        raise typer.BadParameter(msg, param_hint="--odoo-main-path") from error
    if version.semantic not in SpecifierSet(version_specifier):
        msg = (
            f"This command requires an Odoo version matching {version_specifier!r}; "
            f"found Odoo {version.raw} at {odoo_main_repo_path}."
        )
        LOGGER.error(msg)
        raise typer.BadParameter(msg, param_hint="--odoo-main-path")
    return version
