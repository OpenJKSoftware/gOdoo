"""Helps Finding Modules folders and analyzing their dependencies"""

from ast import literal_eval
from logging import getLogger
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Union

LOGGER = getLogger(__name__)


class NotAValidModuleError(ValueError):
    """Raised when a path is not a valid odoo module folder"""


class godooModule:
    """Encapsulates a odoo module folder"""

    def __init__(self, path: Path) -> None:
        """Create a new godooModule instance from a path"""
        self.path = path
        self.validate_is_module()

    def validate_is_module(self):
        """Throws NotAModuleError if path is not a valid odoo module folder"""
        if not self.path.is_dir() or not self.manifest_file.exists():
            raise NotAValidModuleError(f"{self.path} is not a valid odoo module")

    def __repr__(self) -> str:
        return f"godooModule({str(self.path.absolute())})"

    def __eq__(self, __value: object) -> bool:
        if isinstance(__value, godooModule):
            return self.path.absolute() == __value.path.absolute()
        return False

    def __hash__(self) -> int:
        return hash(self.path.absolute())

    @property
    def manifest_file(self) -> Path:
        return self.path / "__manifest__.py"

    @property
    def manifest(self) -> Dict[str, Any]:
        return literal_eval(self.manifest_file.read_text())

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def py_depends(self) -> List[str]:
        return self.manifest.get("external_dependencies", {}).get("python", [])

    @property
    def odoo_depends(self) -> List[str]:
        return self.manifest.get("depends", [])


class godooModules:
    """Abstract interface to Addon-Paths. Finds modules and their dependencies."""

    def __init__(self, addon_paths: Union[List[Path], Path]) -> None:
        if not isinstance(addon_paths, list):
            addon_paths = [addon_paths]
        self.addon_paths = addon_paths
        self.godoo_modules: Dict[str, godooModule] = {}

    def get_modules(
        self, module_names: Optional[List[str]] = None, raise_missing_names=True
    ) -> Generator[godooModule, None, None]:
        """Get all Modules in Addon Paths or only the ones specified in module_names"""
        if module_names:
            for name in module_names:
                try:
                    if module := self.get_module(name):
                        yield module
                except ModuleNotFoundError as e:
                    if raise_missing_names:
                        raise e
                    LOGGER.debug(e.msg)
        else:
            yield from self._get_modules()

    def _get_modules(self) -> Generator[godooModule, None, None]:
        """Generator that Iterates Addon Paths and yields all godooModules found in them."""
        for path in self.addon_paths:
            for addon_folder_child in path.iterdir():
                try:
                    mod = self.godoo_modules.get(addon_folder_child.name)
                    if not mod:
                        mod = godooModule(addon_folder_child)
                        self.godoo_modules[mod.name] = mod
                    if mod.path != addon_folder_child:
                        raise IndexError(
                            f"Module {mod.name} is found in multiple paths:\n{mod.path}\n{addon_folder_child}"
                        )
                    yield mod
                except NotAValidModuleError:
                    # Silently skip dir, as it's not a Odoo Module
                    continue

    def get_module(self, name: str) -> Optional[godooModule]:
        """Get one Specific Module by Name. Returns None if"""
        if mod := self.godoo_modules.get(name):
            return mod
        for mod in self._get_modules():
            if mod.name == name:
                return mod
        raise ModuleNotFoundError(
            f"Module '{name}' not found in Paths: {[str(s.absolute()) for s in self.addon_paths]}"
        )

    def get_module_dependencies(
        self, module: Union[godooModule, List[godooModule]], dont_follow: Optional[List[str]] = None
    ) -> List[godooModule]:
        """Get dependant modules of module(s). Recursively follows dependencies."""
        if isinstance(module, godooModule):
            module = [module]
        deps = []
        for mod in module:
            deps += mod.odoo_depends
        deps = list(set(deps))

        if dont_follow:
            deps = [d for d in deps if d not in dont_follow]
        if deps:
            dont_follow = (dont_follow or []) + deps
            dep_modules = list(self.get_modules(deps, raise_missing_names=False))
            sub_dep_modules = []
            for dep in dep_modules:
                sub_dep_modules += self.get_module_dependencies(dep, dont_follow)
            return list(set(dep_modules + sub_dep_modules))
        return []


def get_zip_addon_path(thirdparty_path: Path) -> Path:
    """Get Zip Addon Path. Basically a constant"""
    return thirdparty_path / "custom"


def get_addon_paths(
    odoo_main_repo: Path,
    workspace_addon_path: Path,
    thirdparty_addon_path: Path,
) -> List[Path]:
    """Get Odoo Addon Paths for odoo.conf.

    Parameters
    ----------
    odoo_main_repo : Path
        Path to main odoo repo
    workspace_addon_path : Path
        Path to workspace addons
    thirdparty_addon_path : Path
        path to git cloned addon repos

    Returns
    -------
    List[Path]
        List of valid addon Paths
    """
    odoo_addon_paths = [odoo_main_repo / "addons", odoo_main_repo / "odoo" / "addons"]
    if godooModules(workspace_addon_path).get_modules():
        odoo_addon_paths.append(workspace_addon_path)
    zip_addon_path = get_zip_addon_path(thirdparty_addon_path)
    zip_addon_repos = [f for f in zip_addon_path.iterdir() if f.is_dir() and next(godooModules(f).get_modules(), None)]
    odoo_addon_paths += zip_addon_repos
    git_thirdparty_addon_repos = [
        p for p in thirdparty_addon_path.iterdir() if next(godooModules(p).get_modules(), None)
    ]
    odoo_addon_paths += git_thirdparty_addon_repos
    return list(set(odoo_addon_paths))
