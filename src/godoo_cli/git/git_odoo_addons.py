"""Odoo addon Git repository management module.

This module provides functionality for managing Odoo addon Git repositories,
including cloning, updating, and configuring repositories based on YAML
configuration files. It supports both direct Git operations and archive-based
downloads for addon repositories.
"""

import concurrent.futures
import logging
from pathlib import Path

from git import Repo

from ..models import GodooGitRepo, GodooManifest
from .git_repo import git_ensure_repo


def git_ensure_repo_matches_manifest(
    target_folder: Path,
    repo_spec: GodooGitRepo,
    default_branch: str = "master",
    force_fetch: bool = False,
    download_archive: bool = False,
    pin_commit: bool = False,
) -> Repo:
    """Clone or update a repository based on a manifest spec."""
    effective_branch = repo_spec.branch or default_branch
    repo = git_ensure_repo(
        target_folder=target_folder,
        repo_src=repo_spec.url,
        branch=effective_branch,
        commit=repo_spec.commit or "",
        pull=repo_spec.ref if force_fetch else "",
        zip_mode=download_archive,
        filter="blob:none",
        single_branch=True,
    )
    if pin_commit and repo:
        repo_spec.commit = repo.head.commit.hexsha
    return repo


LOGGER = logging.getLogger(__name__)


def git_ensure_thirdparty_repos(
    root_folder: Path,
    manifest: GodooManifest,
    download_archive: bool = False,
    pin_commits: bool = False,
    generate_yml_compare_comments: bool = False,
) -> dict[str, Repo]:
    """Clone Thirdparty Addon Repositories specified in Manifest parallely.

    Ensures repo names are prefixed and uses 8 threads to clone.

    Args:
        root_folder: Clone target folder.
        manifest: Parsed GodooManifest instance.
        download_archive: Whether to download as .zip (fast but no history).
        pin_commits: Whether to pin commits in manifest to current HEAD SHA.
        generate_yml_compare_comments: Whether to emit compare URL comments when manifest is saved.

    Returns:
        Dict mapping addon folder names to Repo instances.
    """
    if not manifest.thirdparty:
        LOGGER.info("No Thirdparty Key in manifest. Skipping...")
        return {}

    LOGGER.info("Cloning Thirdparty Addons source.")
    if generate_yml_compare_comments:
        LOGGER.debug("Compare URL comments will be handled during manifest serialization.")
    with concurrent.futures.ThreadPoolExecutor(8) as executor:
        futures: list[tuple[str, concurrent.futures.Future[Repo]]] = []

        for prefix, repo_spec in manifest.iter_thirdparty_repos():
            folder_name = f"{prefix}_{repo_spec.name}"
            futures.append(
                (
                    folder_name,
                    executor.submit(
                        git_ensure_repo_matches_manifest,
                        target_folder=root_folder / folder_name,
                        repo_spec=repo_spec,
                        default_branch=manifest.default_branch,
                        force_fetch=False,
                        download_archive=download_archive,
                        pin_commit=pin_commits,
                    ),
                )
            )

        clone_results: dict[str, Repo] = {folder: future.result() for folder, future in futures if folder}

    return clone_results
