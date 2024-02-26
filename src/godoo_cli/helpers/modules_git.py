"""Functions, related to Module handling in a git Repository context."""
from logging import getLogger
from pathlib import Path
from typing import List

from git import Repo

from .modules import godooModule, godooModules

LOGGER = getLogger(__name__)


def get_changed_modules(
    addon_path: Path,
    diff_ref: str,
) -> List[godooModule]:
    """Get Paths of changed modules since git diff.

    Parameters
    ----------
    addon_path : Path
        Folder in git repo where to look for changes
    diff_ref : str
        Branch or diffable ref for git

    Returns
    -------
    List[godooModule]
        List of Modules where something has changed since git diff
    """
    addon_path = addon_path.absolute()
    repo = Repo(addon_path, search_parent_directories=True)
    git_root = Path(repo.git.rev_parse("--show-toplevel"))
    changed_module_files = []  # All files that changed in the repo and are in addon_path
    diff_lines = repo.git.diff("--name-status", diff_ref).split("\n")
    for change in diff_lines:
        diff_path = git_root / change.split("\t")[1]
        if addon_path in diff_path.parents:
            changed_module_files.append(diff_path)

    changed_modules = []
    odoo_module_paths = {m.path.absolute(): m for m in godooModules(addon_path).get_modules()}
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


def get_changed_modules_and_depends(diff_ref: str, addon_path: Path) -> List[godooModule]:
    """Get Modules that have changed compared to diff_ref and all modules that depend on them."""
    changed_modules = get_changed_modules(addon_path=addon_path, diff_ref=diff_ref)
    if not changed_modules:
        return []
    change_modules_depends = []
    changed_module_names = [p.name for p in changed_modules]
    all_modules = godooModules(addon_path).get_modules()
    for module in all_modules:
        depends = module.odoo_depends
        for depend in depends:
            if depend in changed_module_names:
                change_modules_depends.append(module)
    return list(set(changed_modules + change_modules_depends))
