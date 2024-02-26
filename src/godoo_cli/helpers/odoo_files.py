"""Functions that operate on Odoos Source Code."""
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .system import run_cmd

LOGGER = logging.getLogger(__name__)


@dataclass
class OdooVersion:
    """Structure to hold Odoo version"""

    text: str
    major: int
    minor: int

    @property
    def raw(self):
        return f"{self.major}.{self.minor}"


def odoo_bin_get_version(odoo_main_repo_path: Path) -> OdooVersion:
    """Get Odoo Version by calling 'odoo-bin --version'

    Parameters
    ----------
    odoo_main_repo_path : Path
        Path to odoo-bin folder

    Returns
    -------
    OdooVersion
        odoo-bin --version output parsed into Dataclass
    """
    odoo_bin_path = odoo_main_repo_path / "odoo-bin"
    version_out = run_cmd(f"{odoo_bin_path.absolute()} --version", capture_output=True, text=True)
    vers_match = re.match(r"(?P<text>.*) (?P<major>\d{0,2})\.(?P<minor>\d)", version_out.stdout)
    if vers_match:
        return OdooVersion(
            text=vers_match.group("text"),
            major=int(vers_match.group("major")),
            minor=int(vers_match.group("minor")),
        )
    raise ValueError(f"Could not parse Odoo Version from: '{version_out}'")
