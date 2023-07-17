import json
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
):
    yaml = yaml_roundtrip_loader()
    git_repos = yaml.load(manifest_file.resolve())
    if not git_repos:
        raise FileNotFoundError(f"Couldnt load yml file from: {str(manifest_file)}")

    odoo_data = git_repos.get("odoo")
    if not odoo_data:
        raise KeyError(f"Could not find key 'odoo' in {str(manifest_file)}")

    odoo_url = odoo_data["url"]
    odoo_branch = odoo_data.get("branch", "master")
    odoo_commit = odoo_data.get("commit", "")

    download_stubs(target_folder, force_fetch, download_archive, odoo_data, odoo_branch)

    if add_compare_comment:
        yaml_add_compare_commit(odoo_data, odoo_branch)
    else:
        yaml_remove_compare_commit(odoo_data)

    git_ensure_repo(
        target_folder=target_folder,
        repo_src=odoo_url,
        branch=odoo_branch,
        commit=odoo_commit,
        pull=force_fetch,
        zip_mode=download_archive,
        filter="blob:none",
        single_branch=True,
    )

    yaml.dump(git_repos, manifest_file)


def download_stubs(
    target_folder: Path,
    force_fetch: bool,
    download_archive: bool,
    odoo_data,
    odoo_branch: str,
):
    """Download Odoo stubs for https://github.com/odoo-ide/vscode-odoo

    Hopefully this Method will become obsolete in the future, when the vscode-odoo extension includes stubs in the extension itself.

    Parameters
    ----------
    target_folder : Path
        Stubs will be downloaded to target_folder.parent/odoo-stubs
    force_fetch : bool
        passed down to git_ensure_repo
    download_archive : bool
        passed down to git_ensure_repo
    odoo_data : Dict
        odoo_data from manifest.yml
    odoo_branch : str
        branch to download stubs from
    """
    odoo_stubs = odoo_data.get("stubs")
    if odoo_stubs:
        stub_target_path = target_folder.parent / "odoo-stubs"
        git_ensure_repo(
            target_folder=stub_target_path,
            repo_src=odoo_stubs["url"],
            branch=odoo_branch,
            pull=force_fetch,
            zip_mode=download_archive,
            filter="blob:none",
            single_branch=True,
        )
        if pyright_path := odoo_stubs.get("pyright_conf"):
            json.dump({"stubPath": str(stub_target_path.absolute())}, Path(pyright_path).open("w"))
