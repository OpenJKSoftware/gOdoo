"""Odoo Git repository management module.

This module provides functionality for managing the main Odoo Git repository,
including cloning, updating, and configuring the repository. It supports both
direct Git operations and archive-based downloads.
"""

import logging
from pathlib import Path

from ..helpers.odoo_manifest import yaml_add_compare_commit, yaml_remove_compare_commit, yaml_roundtrip_loader
from .git_repo import git_ensure_repo

LOGGER = logging.getLogger(__name__)


def git_ensure_odoo_repo(
    target_folder: Path,
    manifest_file: Path,
    force_fetch: bool,
    add_compare_comment: bool,
    download_archive: bool,
    pin_commit: bool = False,
):
    """Ensure the Odoo Git repository is properly set up.

    This function manages the Odoo Git repository, handling cloning, updating,
    and configuration based on the provided manifest file.

    Args:
        target_folder: Path where the Odoo repository should be located.
        manifest_file: Path to the manifest file containing repository details.
        force_fetch: Whether to force a fetch from the remote repository.
        add_compare_comment: Whether to add Git comparison comments.
        download_archive: Whether to download as archive instead of Git clone.
        pin_commit: Whether to update the manifest with the current commit.
    """
    yaml = yaml_roundtrip_loader()
    git_repos = yaml.load(manifest_file.resolve())
    if not git_repos:
        msg = f"Couldnt load yml file from: {manifest_file!s}"
        LOGGER.error(msg)
        raise FileNotFoundError(msg)

    odoo_data = git_repos.get("odoo")
    if not odoo_data:
        msg = f"Could not find key 'odoo' in {manifest_file!s}"
        LOGGER.error(msg)
        raise KeyError(msg)

    odoo_url = odoo_data["url"]
    odoo_branch = odoo_data.get("branch", "master")
    odoo_commit = odoo_data.get("commit", "")

    if add_compare_comment:
        yaml_add_compare_commit(odoo_data, odoo_branch)
    else:
        yaml_remove_compare_commit(odoo_data)

    clone_ret = git_ensure_repo(
        target_folder=target_folder,
        repo_src=odoo_url,
        branch=odoo_branch,
        commit=odoo_commit,
        pull=odoo_commit or odoo_branch if force_fetch else "",
        zip_mode=download_archive,
        filter="blob:none",
        single_branch=True,
    )
    if pin_commit and clone_ret:
        odoo_data["commit"] = clone_ret.head.commit.hexsha

    # make sure odoo-bin is executable
    odoo_bin = target_folder / "odoo-bin"
    if odoo_bin.exists():
        LOGGER.debug("chmod odoo-bin +executable")
        odoo_bin.chmod(0o755)
    else:
        LOGGER.warning("Could not find odoo-bin in %s", target_folder)

    yaml.dump(git_repos, manifest_file)
