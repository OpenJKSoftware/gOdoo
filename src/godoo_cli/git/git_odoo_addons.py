"""Odoo addon Git repository management module.

This module provides functionality for managing Odoo addon Git repositories,
including cloning, updating, and configuring repositories based on YAML
configuration files. It supports both direct Git operations and archive-based
downloads for addon repositories.
"""

import concurrent.futures
import logging
from pathlib import Path

from git import Remote, Repo

from ..models import GodooGitRepo, GodooManifest
from .git_repo import git_ensure_repo


def git_ensure_repo_matches_manifest(
    target_folder: Path,
    repo_spec: GodooGitRepo,
    default_branch: str = "master",
    force_fetch: bool = False,
    download_archive: bool = False,
    pin_commit: bool = False,
):
    """Clone or update a repository based on a manifest spec."""
    effective_branch = repo_spec.branch or default_branch
    zip_mode = download_archive and not repo_spec.merge_from
    if download_archive and repo_spec.merge_from:
        LOGGER.info("Disabling zip download for %s because merge_from is specified.", repo_spec.url)
    repo = git_ensure_repo(
        target_folder=target_folder,
        repo_src=repo_spec.url,
        branch=effective_branch,
        commit=repo_spec.commit or "",
        pull=repo_spec.ref if force_fetch else "",
        zip_mode=zip_mode,
        filter="blob:none",
        single_branch=True,
    )
    repo_spec.target_path = target_folder
    if repo:
        if repo_spec.merge_from:
            _merge_sources_into_repo(repo_spec)
        if pin_commit:
            repo_spec.commit = repo.head.commit.hexsha


LOGGER = logging.getLogger(__name__)


def _ensure_remote(repo: Repo, name: str, url: str) -> Remote:
    """Ensure a remote with the given name points to the desired URL."""
    existing = next((remote for remote in repo.remotes if remote.name == name), None)
    if existing:
        urls = set(existing.urls)
        if url not in urls:
            existing.set_url(url)
        return existing
    return repo.create_remote(name, url)


def _merge_sources_into_repo(godoo_repo: GodooGitRepo) -> None:
    """Merge additional sources into the repository as merge commits."""
    repo = godoo_repo.repo

    for merge_spec in godoo_repo.merge_from:
        remote_name = f"merge_from_{merge_spec.url.split('/')[-1].replace('.git', '')}"
        remote = _ensure_remote(repo, remote_name, merge_spec.url)
        fetch_ref = merge_spec.ref
        remote.fetch(fetch_ref)
        merge_target = merge_spec.commit or f"{remote.name}/{merge_spec.branch or fetch_ref}"
        LOGGER.info("Merging ref %s from %s into %s", fetch_ref, merge_spec.url, merge_target)
        repo.git.merge(
            merge_target,
            "--no-ff",
            "--no-edit",
            "--allow-unrelated-histories",
        )


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
