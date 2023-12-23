"""Functions that operate on Odoos Source Code."""
import logging
import re
from ast import literal_eval
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

from git import Repo

from .system import run_cmd

LOGGER = logging.getLogger(__name__)


@dataclass
class OdooVersion:
    """Structure to hold Odoo version"""

    text: str
    major: int
    minor: int

    @property
    def raw(self):
        return f"{self.major}.{self.minor}"


def odoo_bin_get_version(odoo_main_repo_path: Path) -> OdooVersion:
    """Get Odoo Version by calling 'odoo-bin --version'

    Parameters
    ----------
    odoo_main_repo_path : Path
        Path to odoo-bin folder

    Returns
    -------
    OdooVersion
        odoo-bin --version output parsed into Dataclass
    """
    odoo_bin_path = odoo_main_repo_path / "odoo-bin"
    version_out = run_cmd(f"{odoo_bin_path.absolute()} --version", capture_output=True, text=True)
    vers_match = re.match(r"(?P<text>.*) (?P<major>\d{0,2})\.(?P<minor>\d)", version_out.stdout)
    if vers_match:
        return OdooVersion(
            text=vers_match.group("text"),
            major=int(vers_match.group("major")),
            minor=int(vers_match.group("minor")),
        )
    raise ValueError(f"Could not parse Odoo Version from: '{version_out}'")


def get_zip_addon_path(thirdparty_path: Path) -> Path:
    return thirdparty_path / "custom"


def folder_is_odoo_module(folder: Path) -> bool:
    """Wether folder is a valid odoo module.

    Parameters
    ----------
    folder : Path
        Folder to check

    Returns
    -------
    bool
        true if folder contains a module
    """
    return folder.is_dir() and any(folder.glob("__manifest__.py"))


def get_odoo_module_paths(
    search_folders: Union[List[Path], Path],
    module_names: Optional[List[str]] = None,
) -> List[Path]:
    """List all Valid odoo module names in one or many Odoo addons folder.

    Parameters
    ----------
    search_folders : Path
        Folder(s) to search in.
    module_names : Optional[List[str]], optional
        Filter for module names, by default None

    Returns
    -------
    List[Path]
        List of Valid Module Folders within search_folder
    """
    if not isinstance(search_folders, List):
        search_folders = [search_folders]

    module_paths = []
    for folder in search_folders:
        if not folder.exists():
            raise FileNotFoundError(f"Could not find odoo addon folder: '{folder}'")
        for folder in folder.iterdir():
            if folder_is_odoo_module(folder):
                module_paths.append(folder)
    if module_names:
        module_names = [m for m in module_names if m != "base"]
        filtered_module_paths = [p for p in module_paths if p.stem in module_names]
        if unavailable_modules := set(module_names).difference([p.stem for p in filtered_module_paths]):
            LOGGER.warning("Could not find Module folder for: %s", unavailable_modules)
        return filtered_module_paths
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
    for change in repo.git.diff("--name-status", diff_branch).split("\n"):
        path = git_root / change.split("\t")[1]
        if addon_path in path.parents:
            changed_module_files.append(path)

    changed_module_folders = []
    for f in changed_module_files:
        for pf in f.parents:
            if pf.absolute() == addon_path.absolute():
                break
            if pf.absolute() not in changed_module_folders and folder_is_odoo_module(pf):
                changed_module_folders.append(pf.absolute())
    if changed_module_folders:
        LOGGER.debug(
            "Found Modules changed to branch '%s':\n %s",
            diff_branch,
            changed_module_folders,
        )
    return changed_module_folders


def get_depends_of_module(
    all_modules: List[Path],
    module_to_check: Path,
    already_done_modules: Optional[List[Path]] = None,
):
    """Recursively Searches sub dependencies for Odoo modules.


    Parameters
    ----------
    all_modules : List[Path]
        List of Path objects pointing to odoo modules
    module_to_check : Path
        path with odoo module
    already_done_modules : List[Path], optional
        Only used internally for recursion caching, by default None

    Returns
    -------
    List[Path]
        Paths to dependency modules
    """
    manifest_path = module_to_check / "__manifest__.py"

    if not already_done_modules:
        already_done_modules = []
    if module_to_check.absolute() in already_done_modules:
        return []
    already_done_modules.append(module_to_check.absolute())

    manifest = literal_eval(manifest_path.read_text())
    module_depends = manifest.get("depends", [])
    sub_depends = []
    for dep in module_depends:
        dep_path = [p for p in all_modules if p.stem == dep]
        if dep_path:
            dep_path = dep_path[0]
            if dep_path.absolute() in already_done_modules:
                continue
            sub_depends.append(dep_path.absolute())
            sub_depends += get_depends_of_module(all_modules, dep_path, already_done_modules)
        elif dep != "base":
            LOGGER.warn("Could not find Dependency: '%s' in available modules", dep)

    return list(set(sub_depends))


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
    odoo_addon_paths = [odoo_main_repo / "addons"]
    if get_odoo_module_paths(workspace_addon_path):
        odoo_addon_paths.append(workspace_addon_path)
    zip_addon_path = get_zip_addon_path(thirdparty_addon_path)
    zip_addon_repos = [f for f in zip_addon_path.iterdir() if f.is_dir() and get_odoo_module_paths(f)]
    odoo_addon_paths += zip_addon_repos
    git_thirdparty_addon_repos = [
        p for p in thirdparty_addon_path.iterdir() if p.is_dir() and not p.resolve() == zip_addon_path.resolve()
    ]
    odoo_addon_paths += git_thirdparty_addon_repos
    return odoo_addon_paths


def _get_odoo_main_path(addon_paths: List[Path]) -> Path:
    """Get Odoo main path by location of odoo-bin from addon folders.

    Parameters
    ----------
    addon_paths : List[Path]
        List of possible paths

    Returns
    -------
    Path
        Path with odoo-bin and main addons folder
    """
    for path in addon_paths:
        # The main odoo addon path is odoo/addons.
        # so one up should be the launch script
        bin_path = path.parent / "odoo-bin"
        if bin_path.exists() and bin_path.is_file():
            return path


def _get_python_requirements_of_modules(addon_paths: List[Path], filter_module_names: List[str] = None):
    """Install python requirements mentioned in module manifests

    Parameters
    ----------
    addon_paths : List[Path]
        Paths to look for addons. (same as odoo-bin)
    filter_module_names : List[str], optional
        Modules to look for manifests, by default all available modules
    """
    available_modules = get_odoo_module_paths(addon_paths)
    check_modules = get_odoo_module_paths(addon_paths, module_names=filter_module_names)

    if odoo_main_path := _get_odoo_main_path(addon_paths):
        # When the Odoo main path is in the supplied path, exclude those modules.
        # Odoos base requirements are set in their requirements.txt
        LOGGER.debug("Py install: Excluding Odoo main Modules in: '%s'", odoo_main_path)
        check_modules = [mp for mp in check_modules if odoo_main_path not in mp.parents]

    check_modules_dependencies = []
    for module in check_modules:
        check_modules_dependencies += get_depends_of_module(
            available_modules, module, already_done_modules=check_modules_dependencies
        )
    check_modules_dependencies = list(set(check_modules_dependencies))
    LOGGER.debug(
        "adding children of requested modules to check list:\n%s",
        sorted([p.stem for p in check_modules_dependencies]),
    )

    check_modules += check_modules_dependencies

    check_modules = set(check_modules)

    if not check_modules:
        LOGGER.debug("No Modules provided to check for python Requirements")
        return
    python_depends = []
    for module_path in check_modules:
        LOGGER.debug("Checking for External Depends: %s", module_path.absolute())
        manifest_path = module_path / "__manifest__.py"
        manifest = literal_eval(manifest_path.read_text())
        if module_depends := manifest.get("external_dependencies", {}).get("python"):
            python_depends += module_depends

    return list(set(python_depends))
