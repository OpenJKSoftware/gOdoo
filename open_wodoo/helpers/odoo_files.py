"""Functions that operate on Odoos Source Code."""
import logging
from ast import literal_eval
from pathlib import Path
from typing import List

from git import Repo

from ..git import GitUrl

LOGGER = logging.getLogger(__name__)


def get_odoo_addons_in_folder(search_folder: Path) -> List[Path]:
    """List all Valid odoo module names in the Workspace addon folder.

    Parameters
    ----------
    search_folder : Path
        Folder to search in.

    Returns
    -------
    List[Path]
        List of Valid Module Folders within search_folder
    """
    module_paths = []
    if not search_folder.exists():
        return
    for folder in search_folder.iterdir():
        if folder.is_dir() and any(folder.glob("__manifest__.py")):
            module_paths.append(folder)
    return module_paths


def get_changed_modules(
    addon_path: Path,
    diff_branch: str,
) -> List[Path]:
    """Get Paths of changed modules since git diff.

    Parameters
    ----------
    addon_path : Path
        Folder in git repo where to look for changes
    diff_branch : str
        Branch or diffable ref for git

    Returns
    -------
    List[Path]
        List of Paths where something has changed since git diff
    """
    addon_path = addon_path.absolute()
    repo = Repo(addon_path, search_parent_directories=True)
    git_root = Path(repo.git.rev_parse("--show-toplevel"))
    changed_module_files = []
    for l in repo.git.diff("--name-status", diff_branch).split("\n"):
        path = git_root / l.split("\t")[1]
        if addon_path in path.parents:
            changed_module_files.append(path)
    changed_module_folders = list(set([f.parent.absolute() for f in changed_module_files]))
    if changed_module_folders:
        LOGGER.debug(
            "Found Modules changed to branch '%s':\n %s",
            diff_branch,
            "\n".join(["\t" + str(f) for f in changed_module_folders]),
        )
    return changed_module_folders


def get_depends_of_modules(addon_path: Path, in_module_paths: List[Path]) -> List[Path]:
    """Get Modules which do depend on given modules. Recursive.

    Parameters
    ----------
    addon_path : Path
        Folder where to look for Depends of in_module_paths
    in_module_paths : List[Path]
        List of Module Paths of which we want to get the downstream dependencies

    Returns
    -------
    List[Path]
        Union of in_module_paths and all their downstream depends.
    """
    all_modules = get_odoo_addons_in_folder(addon_path)
    if not in_module_paths:
        return
    added = True
    LOGGER.debug("Searching Depends for: %s", ", ".join([str(p) for p in in_module_paths]))
    out_depends = in_module_paths.copy()
    while added:
        added = False
        for module in all_modules:
            manifest_path = module / "__manifest__.py"
            current_module = module.absolute()
            if current_module in out_depends:
                continue
            LOGGER.debug("Loading Manifest: %s", manifest_path.absolute())
            manifest = literal_eval(manifest_path.read_text())
            module_depends = manifest.get("depends", [])
            if any(item.stem in module_depends for item in out_depends):
                out_depends.append(module.absolute())
                added = True
    LOGGER.debug("Found Depends: %s", ", ".join([str(d) for d in out_depends if d not in in_module_paths]))
    return out_depends
