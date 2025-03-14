"""Odoo manifest file management module.

This module provides functionality for managing Odoo manifest files (odoo.yml),
including parsing, updating, and manipulating repository information. It supports
operations like adding comparison URLs, pinning commits, and cleaning up unused
addon folders.
"""

import logging
import shutil
from pathlib import Path

from git import Repo
from ruamel.yaml import YAML

from ..git.git_url import GitUrl

LOGGER = logging.getLogger(__name__)


def remove_unused_folders(
    thirdparty_addon_path: Path, thirdparty_repos: dict[str, list[dict]], keep_folders: list[Path]
):
    """Remove folders that are not included in git_repos anymore.

    Parameters
    ----------
    thirdparty_addon_path : Path
        Folder to check for deletions
    thirdparty_repos : Dict
        Dict of Prefix:[dict[url],..]
    keep_folders : List[Path]
        List of folders to keep regardless of their presence in thirdparty_repos
    """
    allowed_folders = []
    keep_folders_absolute = [p.absolute() for p in keep_folders]
    for prefix in thirdparty_repos:
        for repo in thirdparty_repos[prefix]:
            repo_url = GitUrl(repo["url"])
            allowed_folders.append(f"{prefix}_{repo_url.name}")
    for folder in thirdparty_addon_path.iterdir():
        if not folder.is_dir() or folder.absolute() in keep_folders_absolute:
            continue
        if folder.stem not in allowed_folders:
            LOGGER.info("Removing unspecified Addon Folder: %s", folder)
            shutil.rmtree(folder)


def update_yml(
    repo_yml: dict,
    clone_results: dict[str, Repo],
    generate_yml_compare_comments: bool = False,
    generate_yml_commit_pins: bool = False,
):
    """Process yaml after thirdparty clone.

    Parameters
    ----------
    repo_yml : Yaml Dict
        Ruamel Yaml Dict of prefix and list of repos (url,commit,branch)
    clone_results: dict[str,Commit]
        Git url to currently cloned commit. Gets returned from Git Downloader
    generate_yml_compare_comments : bool, optional
        add github compare links as comment to repo yml, by default False
    generate_yml_commit_pins: bool, optional
        adds commit pins to currently checked out head commit
    """
    thirdparty_repos = repo_yml["thirdparty"]
    odoo_default_branch = repo_yml["odoo"].get("branch")
    if not odoo_default_branch:
        LOGGER.error("Odoo Key in manifest missing branch argument.")
        return

    for prefix in thirdparty_repos:
        for repo in thirdparty_repos[prefix]:
            if generate_yml_commit_pins and (c := clone_results.get(repo.get("url"))):
                repo["commit"] = c.head.commit.hexsha
            if generate_yml_compare_comments:
                yaml_add_compare_commit(repo, odoo_default_branch)
            else:
                yaml_remove_compare_commit(repo)


def yaml_add_compare_commit(repo_dict: dict, compare_target: str):
    """Add comment with compare URL to repo.

    Parameters
    ----------
    repo_dict : dict
        YAML dict containing repository URL and commit information
    compare_target : str
        Git reference to compare against
    """
    git_url = GitUrl(repo_dict["url"])
    try:
        compare_url = git_url.get_compare_url(repo_dict["commit"], compare_target)
        repo_dict.yaml_add_eol_comment(compare_url, "commit")
    except Exception as e:
        msg = f"Cannot Generate compare URL for: {git_url.url}"
        LOGGER.warning(msg)
        LOGGER.debug(e)


def yaml_remove_compare_commit(repo_dict: dict):
    """Remove comments that have /compare/ in them.

    Parameters
    ----------
    repo_dict : dict
        YAML dict containing repository information and comments
    """
    del_list = []
    for target, comments in repo_dict.ca.items.items():
        for subcomment in comments:
            if subcomment and "/compare/" in subcomment.value:
                del_list.append(target)

    for target in del_list:
        del repo_dict.ca.items[target]


def yaml_roundtrip_loader() -> YAML:
    """Return Ruamel Roundtrip loader.

    Returns:
    -------
    YAML
        Yaml Loader
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml
