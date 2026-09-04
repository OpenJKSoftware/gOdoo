"""Resolve Python dependencies declared by Odoo modules."""

import logging
from pathlib import Path
from types import GeneratorType

from ..models import GodooModule, GodooModules
from .odoo_command import OdooCommand, odoo_command_argv
from .pip import pip_command, pip_install
from .system import run_cmd

LOGGER = logging.getLogger(__name__)


def install_base_python_reqs(odoo_install_folder: Path):
    """Install Odoo's base Python requirements."""
    reqs_file = odoo_install_folder / "requirements.txt"
    if reqs_file.exists():
        LOGGER.debug("Installing base Odoo Python requirements from %s", reqs_file)
        return run_cmd(f"{pip_command()} install -r {reqs_file}")
    else:
        LOGGER.warning("Odoo requirements.txt file not found at %s", reqs_file)


def install_py_reqs_for_modules(modules: list[GodooModule], module_reg: GodooModules):
    """Install Python dependencies declared by the given modules."""
    reqs: list[str] = []
    if isinstance(modules, GeneratorType):
        modules = list(modules)
    all_modules = modules + module_reg.get_module_dependencies(modules)
    all_modules = list(set(all_modules))
    for mod in all_modules:
        reqs += mod.py_depends
    if reqs:
        return pip_install(list(set(reqs)))


def install_py_reqs_by_odoo_cmd(addon_paths: list[Path], odoo_bin_cmd: OdooCommand):
    """Install dependencies for modules selected in an Odoo command."""
    argv = odoo_command_argv(odoo_bin_cmd)
    install_modules = [
        module
        for option, modules in zip(argv, argv[1:])
        if option in ("--init", "-i", "--load")
        for module in modules.split(",")
    ]
    if install_modules:
        LOGGER.debug("Found Modules to install in odoo-bin command: %s", install_modules)
        module_reg = GodooModules(addon_paths)
        modules = [module_reg.get_module(m) for m in install_modules]
        modules = [m for m in modules if m]
        return install_py_reqs_for_modules(modules, module_reg)
