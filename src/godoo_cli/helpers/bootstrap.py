import logging
import re
import subprocess
from ast import literal_eval
from pathlib import Path
from typing import List

from .odoo_files import _get_python_requirements_of_modules
from .system import run_cmd

LOGGER = logging.getLogger(__name__)


def _install_py_reqs_for_modules(addon_paths: List[Path], module_names: List[str] = None):
    """Install Python Requirements mentioned in odoo module manifests of given modules

    Parameters
    ----------
    addon_paths : List[Path]
        odoo-bin addons path
    module_names : List[str], Optional
        List of modules to filter for, by default "all available modules"

    Returns
    -------
    CompletedProcess
    """
    py_reqs = _get_python_requirements_of_modules(addon_paths=addon_paths, filter_module_names=module_names)
    if py_reqs:
        installed_packages = run_cmd(
            "pip list --format json --disable-pip-version-check", check=True, shell=True, stdout=subprocess.PIPE
        ).stdout.decode("utf-8")
        installed_packages = [m.get("name") for m in literal_eval(installed_packages)]
        if missing_packages := [p for p in py_reqs if p not in installed_packages]:
            LOGGER.info("Installing Python requirements: '%s'", ", ".join(missing_packages))
            return run_cmd(f"pip install {' '.join(missing_packages)} --disable-pip-version-check", shell=True)
    LOGGER.debug("No py Requirements to be installed")


def _install_py_reqs_by_odoo_cmd(addon_paths: List[Path], odoo_bin_cmd: str):
    """Install Python reqs for modules mentioned in odoo-bin commandline --init or -i directives.

    Parameters
    ----------
    addon_paths : List[Path]
        odoo-bin addons-path
    odoo_bin_cmd : str
        odoo-bin commandline

    Returns
    -------
    CompletedProcess
    """
    install_modules = []
    for m in re.finditer(r'(--init|-i) "?([^ \n]+)"?', odoo_bin_cmd):
        install_modules += m.group(2).split(",")
    if install_modules:
        return _install_py_reqs_for_modules(addon_paths=addon_paths, module_names=install_modules)
