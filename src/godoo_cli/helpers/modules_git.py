"""Resolve changed Odoo modules from Git history."""

from logging import getLogger
from pathlib import Path

from ..git import repository
from ..models import GodooModule, GodooModules

LOGGER = getLogger(__name__)


def get_changed_modules(
    addon_path: Path,
    diff_ref: str,
) -> list[GodooModule]:
    """Return modules changed from a Git reference."""
    addon_path = addon_path.absolute()
    repo = repository(addon_path)
    changed_module_files = []  # All files that changed in the repo and are in addon_path
    for diff_path in repo.changed_paths(diff_ref):
        if addon_path in diff_path.parents:
            changed_module_files.append(diff_path)

    changed_modules = []
    odoo_module_paths = {m.path.absolute(): m for m in GodooModules(addon_path).get_modules()}
    for f in changed_module_files:
        for pf in f.parents:
            if m := odoo_module_paths.get(pf.absolute()):
                changed_modules.append(m)
                break
            if pf.absolute() == addon_path:
                break
    if changed_modules:
        LOGGER.debug(
            "Found Modules changed to branch '%s':\n %s",
            diff_ref,
            changed_modules,
        )
    return changed_modules


def get_changed_modules_and_depends(diff_ref: str, addon_path: Path) -> list[GodooModule]:
    """Return changed modules and every module that depends on them."""
    changed_modules = get_changed_modules(addon_path=addon_path, diff_ref=diff_ref)
    if not changed_modules:
        return []
    change_modules_depends = []
    changed_module_names = [p.name for p in changed_modules]
    all_modules = GodooModules(addon_path).get_modules()
    for module in all_modules:
        depends = module.odoo_depends
        for depend in depends:
            if depend in changed_module_names:
                change_modules_depends.append(module)
    return list(set(changed_modules + change_modules_depends))
