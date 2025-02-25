"""Helps Finding Modules folders and analyzing their dependencies."""

from ast import literal_eval
from logging import getLogger
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Union

LOGGER = getLogger(__name__)


class NotAValidModuleError(ValueError):
    """Raised when a path is not a valid odoo module folder."""


class godooModule:
    """Encapsulates a odoo module folder."""

    def __init__(self, path: Path) -> None:
        """Create a new godooModule instance from a path."""
        self.path = path
        self.validate_is_module()

    def validate_is_module(self):
        """Throws NotAModuleError if path is not a valid odoo module folder."""
        if not self.path.is_dir() or not self.manifest_file.exists():
            raise NotAValidModuleError(f"{self.path} is not a valid odoo module")

    def __repr__(self) -> str:
        """Return a string representation of the godooModule instance."""
        return f"godooModule({str(self.path.name)})"

    def __eq__(self, __value: object) -> bool:
        """Compare two godooModule instances for equality based on their absolute paths."""
        if isinstance(__value, godooModule):
            return self.path.absolute() == __value.path.absolute()
        return False

    def __hash__(self) -> int:
        """Return a hash value based on the module's absolute path."""
        return hash(self.path.absolute())

    @property
    def manifest_file(self) -> Path:
        """Path to the module's manifest file (__manifest__.py)."""
        return self.path / "__manifest__.py"

    @property
    def manifest(self) -> Dict[str, Any]:
        """Dictionary containing the parsed contents of the module's manifest file."""
        return literal_eval(self.manifest_file.read_text())

    @property
    def name(self) -> str:
        """The name of the module, derived from the directory name."""
        return self.path.stem

    @property
    def py_depends(self) -> List[str]:
        """List of Python package dependencies required by this module."""
        module_depends = self.manifest.get("external_dependencies", {}).get("python", [])
        return module_depends

    @property
    def odoo_depends(self) -> List[str]:
        """List of Odoo module dependencies required by this module."""
        return self.manifest.get("depends", [])


class godooModules:
    """Abstract interface to Addon-Paths. Finds modules and their dependencies."""

    def __init__(self, addon_paths: Union[List[Path], Path]) -> None:
        """Initialize a godooModules instance with one or more addon paths.

        Args:
            addon_paths: Single path or list of paths to search for Odoo modules.
        """
        if not isinstance(addon_paths, list):
            addon_paths = [addon_paths]
        self.addon_paths = addon_paths
        self.godoo_modules: Dict[str, godooModule] = {}

    def get_modules(
        self, module_names: Optional[List[str]] = None, raise_missing_names=True
    ) -> Generator[godooModule, None, None]:
        """Get all Modules in Addon Paths or only the ones specified in module_names."""
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
        """Get one Specific Module by Name. Returns None if not found."""
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
    """Get the path where zip-based addons are stored.

    This function returns the standard location for addons that are installed from zip files
    within the thirdparty addons directory.

    Args:
        thirdparty_path: Base path for thirdparty addons.

    Returns:
        Path: The 'custom' subdirectory within the thirdparty path where zip-based addons are stored.
    """
    return thirdparty_path / "custom"


def get_addon_paths(
    odoo_main_repo: Path,
    workspace_addon_path: Path,
    thirdparty_addon_path: Path,
) -> List[Path]:
    """Get all valid Odoo addon paths for the odoo.conf addons_path setting.

    This function collects all valid addon paths from:
    - Core Odoo addons (from odoo_main_repo)
    - Workspace addons (if any valid modules exist)
    - Thirdparty addons (both zip-based and git-based)

    The function validates each path to ensure it contains valid Odoo modules
    before including it in the result.

    Args:
        odoo_main_repo: Path to the main Odoo repository.
        workspace_addon_path: Path to the workspace addons directory.
        thirdparty_addon_path: Path to the thirdparty addons directory.

    Returns:
        List[Path]: List of unique, valid addon paths that should be included
            in the Odoo addons_path configuration.
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
