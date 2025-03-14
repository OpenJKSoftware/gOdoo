"""Python dependency management for Odoo modules.

This module provides functionality for managing Python dependencies required by
Odoo modules, including detecting dependencies from module manifests and
installing them using pip. It ensures that all required Python packages are
available for Odoo modules to function properly.
"""

import logging
import re
from pathlib import Path
from types import GeneratorType

from .modules import GodooModule, GodooModules
from .pip import pip_install

LOGGER = logging.getLogger(__name__)


def _install_py_reqs_for_modules(modules: list[GodooModule], module_reg: GodooModules):
    """Install Python Requirements mentioned in odoo module manifests of given modules.

    Parameters
    ----------
    modules : List[godooModules]
        List of modules in wiich to check __manifest__.py for python requirements
    module_reg : godooModules
        Module Registry for Dependency Search

    Returns:
    -------
    CompletedProcess
    """
    reqs: list[str] = []
    if isinstance(modules, GeneratorType):
        modules = list(modules)
    all_modules = modules + module_reg.get_module_dependencies(modules)
    all_modules = list(set(all_modules))
    for mod in all_modules:
        reqs += mod.py_depends
    if reqs:
        return pip_install(list(set(reqs)))


def _install_py_reqs_by_odoo_cmd(addon_paths: list[Path], odoo_bin_cmd: str):
    """Install Python reqs for modules mentioned in odoo-bin commandline --init or -i directives.

    Parameters
    ----------
    addon_paths : List[Path]
        odoo-bin addons-path
    odoo_bin_cmd : str
        odoo-bin commandline

    Returns:
    -------
    CompletedProcess
    """
    install_modules = []
    for m in re.finditer(r'(--init|-i|--load) "?([^ \n]+)"?', odoo_bin_cmd):
        install_modules += m.group(2).split(",")
    if install_modules:
        LOGGER.debug("Found Modules to install in odoo-bin command: %s", install_modules)
        module_reg = GodooModules(addon_paths)
        modules = [module_reg.get_module(m) for m in install_modules]
        modules = [m for m in modules if m]
        return _install_py_reqs_for_modules(modules, module_reg)
