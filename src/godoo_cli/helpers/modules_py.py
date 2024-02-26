import logging
import re
from pathlib import Path
from typing import List

from .modules import godooModules
from .system import pip_install

LOGGER = logging.getLogger(__name__)


def _install_py_reqs_for_modules(modules: List[godooModules]):
    """Install Python Requirements mentioned in odoo module manifests of given modules

    Parameters
    ----------
    modules : List[godooModules]
        List of modules in wiich to check __manifest__.py for python requirements

    Returns
    -------
    CompletedProcess
    """
    reqs = []
    for mod in modules:
        reqs += mod.py_depends
    if reqs:
        return pip_install(reqs)


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
        module_reg = godooModules(addon_paths)
        modules = [module_reg.get_module(m) for m in install_modules]
        return _install_py_reqs_for_modules(modules)
