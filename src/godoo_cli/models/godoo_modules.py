"""Discover Odoo modules and resolve their dependencies."""

from ast import literal_eval
from collections.abc import Generator
from functools import cached_property
from logging import getLogger
from pathlib import Path
from typing import Any, Optional, Union

LOGGER = getLogger(__name__)
NO_MODULE_PATHS: set[Path] = set()
SPECIAL_MODULES = {"base", "studio_customization"}


class NotAValidModuleError(ValueError):
    """Raised when a path is not a valid Odoo module directory."""


class GodooModule:
    """Represent one Odoo module directory."""

    def __init__(self, path: Path) -> None:
        """Create a module from its source path."""
        self.path = path
        self.validate_is_module()

    def __repr__(self) -> str:
        """Return a compact representation of the module."""
        return f"godooModule({self.path.name!s})"

    def __eq__(self, __value: object) -> bool:
        """Compare modules by absolute source path."""
        if isinstance(__value, GodooModule):
            return self.path.absolute() == __value.path.absolute()
        return False

    def __hash__(self) -> int:
        """Hash the module's absolute source path."""
        return hash(self.path.absolute())

    @property
    def manifest_file(self) -> Path:
        """Return the module's ``__manifest__.py`` path."""
        return self.path / "__manifest__.py"

    @cached_property
    def manifest(self) -> dict[str, Any]:
        """Return the parsed module manifest."""
        return literal_eval(self.manifest_file.read_text(encoding="utf-8"))

    @property
    def version(self) -> str:
        """Return the version declared in the module manifest."""
        return self.manifest.get("version", "unknown")

    @property
    def name(self) -> str:
        """Return the module name derived from its directory."""
        return self.path.stem

    @property
    def py_depends(self) -> list[str]:
        """Return Python packages required by this module."""
        module_depends = self.manifest.get("external_dependencies", {}).get("python", [])
        return module_depends

    @property
    def odoo_depends(self) -> list[str]:
        """Return Odoo modules required by this module."""
        return self.manifest.get("depends", [])

    def validate_is_module(self):
        """Reject paths that are not Odoo module directories."""
        if not self.path.is_dir():
            msg = f"{self.path} is not a directory"
            raise NotAValidModuleError(msg)
        if not self.manifest_file.exists():
            msg = f"{self.path} is not a valid odoo module"
            raise NotAValidModuleError(msg)


class GodooModules:
    """Discover modules and dependencies across addon paths."""

    def __init__(self, addon_paths: Union[list[Path], Path]) -> None:
        """Search one or more addon paths."""
        if not isinstance(addon_paths, list):
            addon_paths = [addon_paths]
        self.addon_paths = addon_paths
        self.godoo_modules: dict[str, GodooModule] = {}

    def get_modules(
        self, module_names: Optional[list[str]] = None, raise_missing_names: bool = True
    ) -> Generator[GodooModule, None, None]:
        """Yield all modules or only those explicitly requested."""
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

    def _get_modules(self) -> Generator[GodooModule, None, None]:
        """Yield every valid module found below the addon paths."""
        for path in self.addon_paths:
            for addon_folder_child in path.iterdir():
                if addon_folder_child in NO_MODULE_PATHS:
                    # Skip paths that are already known to not be modules
                    continue
                try:
                    mod = self.godoo_modules.get(addon_folder_child.name)
                    if not mod:
                        mod = GodooModule(addon_folder_child)
                        self.godoo_modules[mod.name] = mod
                    if mod.path != addon_folder_child:
                        msg = f"Module {mod.name} is found in multiple paths:\n{mod.path}\n{addon_folder_child}"
                        LOGGER.error(msg)
                        raise IndexError(msg)
                    yield mod
                except NotAValidModuleError:
                    # Silently skip dir, as it's not a Odoo Module
                    NO_MODULE_PATHS.add(addon_folder_child)
                    continue

    def get_module(self, name: str) -> Optional[GodooModule]:
        """Return one named module, ignoring built-in special modules."""
        if name in SPECIAL_MODULES:
            return None
        if mod := self.godoo_modules.get(name):
            return mod
        for mod in self._get_modules():
            if mod.name == name:
                return mod
        msg = f"Module '{name}' not found in addon-paths"
        LOGGER.error(msg)
        raise ModuleNotFoundError(msg)

    def get_module_dependencies(
        self, module: Union[GodooModule, list[GodooModule]], dont_follow: Optional[list[str]] = None
    ) -> list[GodooModule]:
        """Return module dependencies recursively."""
        if isinstance(module, GodooModule):
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
