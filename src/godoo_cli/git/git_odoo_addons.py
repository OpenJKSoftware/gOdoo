import concurrent.futures
import logging
from pathlib import Path
from typing import Dict

from git import Commit

from ..helpers.odoo_manifest import update_yml, yaml_roundtrip_loader
from .git_repo import git_ensure_repo
from .git_url import GitUrl

LOGGER = logging.getLogger(__name__)


def git_ensure_addon_repos(
    root_folder: Path,
    git_yml_path: Path,
    generate_yml_compare_comments: bool = False,
    download_archive: bool = False,
):
    """
    Clone repos specified in Yml.

    Parameters
    ----------
    root_folder : Path
        clone target folder
    git_yml_path : Path
        yml describing what to clone
    generate_yml_compare_comments : bool, optional
        wether to add three dot compare on remote to repo urls
    download_archive : bool, optional
        wether to download as .zip (fast but no history), by default False
    """
    yaml = yaml_roundtrip_loader()
    git_repos = yaml.load(git_yml_path.resolve())
    if _git_clone_addon_repos(root_folder=root_folder, git_repos=git_repos, download_archive=download_archive):
        update_yml(git_repos, generate_yml_compare_comments)
        LOGGER.info("Updating Git Thirdparty Repo Commit hashes")
        yaml.dump(git_repos, git_yml_path)

    return git_repos


def _git_clone_addon_repos(
    root_folder: Path,
    git_repos: Dict[str, Dict[str, str]],
    download_archive: bool = False,
) -> Dict[str, Commit]:
    """
    Clones Git repos specified in dict into Root folder.
    Ensures repo names are prefixed and uses 8 threads to clone.

    Parameters
    ----------
    root_folder : Path
        clone target folder
    git_repos : Dict[str, Dict[str, str]]
        dict of {parent_folder_name:{"url":clone_url,"branch":clone_branch,"commit":specific_commit_to_clone}}
        branch and commit are optional
        branch defaults to odoo branch from spec file
    download_archive : bool, optional
        wether to download as .zip (fast but no history), by default False
    Returns
    -------
    Dict[str:Commit]
        dict of {git_src_url:HeadCommit}
    """
    default_branch = git_repos["odoo"].get("branch")
    LOGGER.info("Cloning Thirdparty Addons source.")
    with concurrent.futures.ThreadPoolExecutor(8) as executor:
        futures = []
        thirdparty_repos = git_repos.get("thirdparty")
        if not thirdparty_repos:
            LOGGER.info("No Thirdparty Key in manifest. Skipping...")
            return
        for prefix, repos in thirdparty_repos.items():
            for repo in repos:
                repo_url = GitUrl(repo["url"])
                name = f"{prefix}_{repo_url.name}"
                futures.append(
                    executor.submit(
                        git_ensure_repo,
                        target_folder=Path(root_folder / name),
                        repo_src=repo_url.url,
                        branch=repo.get("branch", default_branch),
                        commit=repo.get("commit"),
                        zip_mode=download_archive,
                        filter="blob:none",
                        single_branch=True,
                    )
                )
        clone_results = [f.result() for f in futures]
        clone_results = {r[0]: r[1] for r in clone_results if r}
    return clone_results
