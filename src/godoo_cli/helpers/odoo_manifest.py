import logging
import shutil
from pathlib import Path
from typing import List

from ruamel.yaml import YAML

from ..git.git_url import GitUrl

LOGGER = logging.getLogger(__name__)


def remove_unused_folders(thirdparty_addon_path: Path, thirdparty_repos, keep_folders: List[Path]):
    """Remove folders that are not included in git_repos anymore

    Parameters
    ----------
    thirdparty_addon_path : Path
        Folder to check for deletions
    thirdparty_repos : Dict
        Dict of Prefix:[dict[url],..]
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
    repo_yml,
    generate_yml_compare_comments: bool = False,
):
    """Process yaml after thirdparty clone.

    Parameters
    ----------
    repo_yml : Yaml Dict
        Ruamel Yaml Dict of prefix and list of repos (url,commit,branch)
    generate_yml_compare_comments : bool, optional
        add github compare links as comment to repo yml, by default False
    """
    thirdparty_repos = repo_yml["thirdparty"]
    odoo_default_branch = repo_yml["odoo"].get("branch")
    if not odoo_default_branch:
        LOGGER.error("Odoo Key in manifest missing branch argument.")
        return

    for prefix in thirdparty_repos:
        for repo in thirdparty_repos[prefix]:
            if generate_yml_compare_comments:
                yaml_add_compare_commit(repo, odoo_default_branch)
            else:
                yaml_remove_compare_commit(repo)


def yaml_add_compare_commit(repo_dict, compare_target: str):
    """Add comment with Compare URL to Repo:

    Parameters
    ----------
    repo_dict : _type_
        Yaml Dict of url and commit
    compare_target : str
        git ref to compare to
    """
    git_url = GitUrl(repo_dict["url"])
    try:
        compare_url = git_url.get_compare_url(repo_dict["commit"], compare_target)
        repo_dict.yaml_add_eol_comment(compare_url, "commit")
    except Exception as e:
        LOGGER.warn(f"Cannot Generate compare URL for: {git_url.url}")
        LOGGER.debug(e)


def yaml_remove_compare_commit(repo_dict):
    """Remove Comments that have /compare/ in them.

    Parameters
    ----------
    repo_dict : RuamelYaml Dict
        yaml dict
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

    Returns
    -------
    YAML
        Yaml Loader
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml
