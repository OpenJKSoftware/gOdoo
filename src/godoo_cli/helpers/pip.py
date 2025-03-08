"""Helper functions for pip operations."""

import json
import os
import subprocess
import sys
from logging import getLogger
from typing import List

from godoo_cli.helpers.system import run_cmd

LOGGER = getLogger(__name__)


def _has_uv() -> bool:
    """Check if uv is available."""
    try:
        run_cmd("uv --version", check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError:
        return False


def _check_pip_command() -> str:
    """Check which pip command is available and return the appropriate command string.

    Returns:
    -------
    str
        The command to use for pip operations ('python -m pip' or 'uv pip')

    Raises:
    ------
    RuntimeError
        If no supported pip command is available
    """
    # Check if uv is available
    if _has_uv():
        # If we have a virtual environment, use uv pip
        if os.getenv("VIRTUAL_ENV"):
            return "uv pip"

    # Try python -m pip as fallback
    try:
        run_cmd(
            f"{sys.executable} -m pip --version",
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return f"{sys.executable} -m pip"
    except subprocess.CalledProcessError as e:
        raise RuntimeError("No pip command available. Please ensure pip is installed or use a uv venv.") from e


def pip_install(package_names: List[str]):
    """Ensure pip packages are installed if not already present."""
    # Some packages have different names on pypi and in odoo Manifests. Key is Odoo manigest, Value is pypi
    odoo_wrong_pkg_names = {
        "ldap": "python-ldap",
    }
    package_names = [odoo_wrong_pkg_names.get(p, p) for p in package_names]

    LOGGER.debug("Ensuring Pip Packages are installed:\n%s", package_names)

    # Get the appropriate pip command
    pip_cmd = _check_pip_command()

    installed_packages = run_cmd(
        f"{pip_cmd} list --format json",
        check=True,
        shell=True,
        stdout=subprocess.PIPE,
    ).stdout.decode("utf-8")
    installed_packages = json.loads(installed_packages)
    installed_packages = [p.get("name") for p in installed_packages]
    if missing_packages := [p for p in package_names if p not in installed_packages]:
        LOGGER.info("Installing Python requirements: %s", missing_packages)
        res = run_cmd(f"{pip_cmd} install {' '.join(missing_packages)}", shell=True)
        if res.returncode != 0:
            raise FileNotFoundError(f"Package installation error for: {missing_packages}")
        return res
