import configparser
import logging
import shutil
import zipfile
from enum import Enum
from pathlib import Path
from typing import List

import typer

from .helper_cli import typer_unpacker
from .helper_odoo_files import get_odoo_addons_in_folder
from .helper_git import (
    GitUrl,
    git_clone_thirdparty_repos,
    git_download_zip,
    git_ensure_cloned,
    yaml_add_compare_commit,
    yaml_remove_compare_commit,
    yaml_roundtrip_loader,
)

LOGGER = logging.getLogger(__name__)


class UpdateMode(str, Enum):
    all = "all"
    zip = "zip"
    odoo = "odoo"
    thirdparty = "thirdparty"


def clone_odoo(
    target_folder: Path,
    repo_spec_file: Path,
    force_fetch: bool,
    add_compare_comment: bool,
    download_archive: bool,
):
    yaml = yaml_roundtrip_loader()
    git_repos = yaml.load(repo_spec_file.resolve())
    if not git_repos:
        raise FileNotFoundError(f"Couldnt load yml file from: {str(repo_spec_file)}")

    odoo_data = git_repos.get("odoo")
    if not odoo_data:
        raise KeyError(f"Could not find key 'odoo' in {str(repo_spec_file)}")

    odoo_url = odoo_data["url"]
    odoo_branch = odoo_data.get("branch", "master")
    odoo_commit = odoo_data.get("commit", "")
    if add_compare_comment:
        yaml_add_compare_commit(odoo_data, odoo_branch)
    else:
        yaml_remove_compare_commit(odoo_data)

    if download_archive:
        if target_folder.exists() and next(target_folder.iterdir(), None):
            LOGGER.debug("Clearing Odoo Target folder: %s", target_folder)
            shutil.rmtree(target_folder)
        git_download_zip(odoo_url, target_folder, odoo_branch, odoo_commit)
    else:
        LOGGER.info(
            "Ensuring Odoo Source equals repospec: URL: %s, Branch: %s, Commit: %s",
            odoo_url,
            odoo_branch,
            odoo_commit,
        )
        git_ensure_cloned(
            target_folder,
            repo_src=odoo_url,
            branch=odoo_branch,
            commit=odoo_commit,
            pull=odoo_branch if force_fetch else "",
            filter="blob:none",
            single_branch=True,
        )
    yaml.dump(git_repos, repo_spec_file)


def unzip_addons(zip_folder: Path, target_addon_folder: Path, remove_excess: bool = False):
    """Take .zip files from zip_folder and extracts them into the odoo addons folder.

    Parameters
    ----------
    zip_folder : Path
        Where to look for zip files
    target_addon_folder : Path
        where to place them
    remove_excess : bool , optional
        remove all and then unzip, by default False
    """
    if remove_excess:
        for folder in target_addon_folder.iterdir():
            shutil.rmtree(folder)
    LOGGER.info("Extracting Zip Addons to: %s", target_addon_folder)
    for zip_file in zip_folder.glob("*.zip"):
        target_addon_folder.mkdir(exist_ok=True, parents=True)
        LOGGER.info("Extracting Zip Addon: %s", zip_file)
        with zipfile.ZipFile(zip_file, "r") as zip_ref:
            zip_ref.extractall(target_addon_folder)


def update_odoo_conf_addon_paths(odoo_conf: Path, addon_paths: List[Path]):
    """Update Odoo.Conf with Addon Paths

    Parameters
    ----------
    odoo_conf : Path
        odoo.conf location
    addon_paths : List[Path]
        list of paths
    """
    config = configparser.ConfigParser()
    config.read(odoo_conf)
    addon_paths = ",".join([str(p.absolute()) for p in addon_paths])
    config["options"]["addons_path"] = addon_paths
    LOGGER.info("Writing Addon Paths to Odoo Config.")
    LOGGER.debug(addon_paths)
    config.write(odoo_conf.open("w"))


def _git_remove_unused_folders(thirdparty_addon_path: Path, thirdparty_repos, keep_folders: List[Path]):
    """Remove folders that are not included in git_repos anymore

    Parameters
    ----------
    thirdparty_addon_path : Path
        Folder to check for deletions
    thirdparty_repos : _type_
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
        if not folder.stem in allowed_folders:
            LOGGER.info("Removing unspecified Addon Folder: %s", folder)
            shutil.rmtree(folder)


@typer_unpacker
def update_addons(
    ctx: typer.Context,
    thirdparty_addon_path: Path = typer.Option(
        ..., envvar="ODOO_THIRDPARTY_LOCATION", help="Root folder of the Thirdparty addon repos"
    ),
    thirdparty_zip_source: Path = typer.Option(
        ..., envvar="ODOO_THIRDPARTY_ZIP_LOCATION", help="Source folder, where to look for Addon zips"
    ),
    repospec_yml: Path = typer.Option(
        ..., envvar="ODOO_GITSPEC", help="Git.yml file, that specified what to download with wich prefix"
    ),
    update_mode: UpdateMode = typer.Option(UpdateMode.all, help="What to Update"),
    add_compare_comments: bool = typer.Option(
        False, help="Wether to add github.com three dot compare links as comments."
    ),
    remove_unspecified_addons: bool = typer.Option(
        False, help="Remove Addon folders that are not in YML or thirdparty.zip"
    ),
    force_fetch: bool = typer.Option(
        False,
        help="Forces origin fetch, regardless of current branch or commit sha (may be slow)",
    ),
):
    """Update Odoo Source and Thirdparty source

    Parameters
    ----------
    ctx : typer.Context
        Typer Context
    thirdparty_addon_path : Path, optional
        Root folder of the Thirdparty addon repos, by default envvar="ODOO_THIRDPARTY_LOCATION"
    thirdparty_zip_source : Path, optional
        Source folder, where to look for Addon zips, by default envvar="ODOO_THIRDPARTY_ZIP_LOCATION"
    repospec_yml : Path, optional
        Git.yml file, that specified what to download with wich prefix, by default envvar="ODOO_GITSPEC"
    update_mode : UpdateMode, optional
        What to Update, by default UpdateMode.all
    add_compare_comments : bool, optional
        Wether to add github.com three dot compare links as comments., by default False
    remove_unspecified_addons : bool, optional
        Remove Addon folders that are not in YML or thirdparty.zip, by default False
    force_fetch : bool, optional
        Forces origin fetch, regardless of current branch or commit sha, by default False
    """
    LOGGER.info("Updating Addon Repos")
    zip_addon_path = thirdparty_addon_path / "custom"

    if update_mode in ["all", "zip"]:
        unzip_addons(thirdparty_zip_source, zip_addon_path)

    if update_mode in ["all", "odoo"]:
        clone_odoo(
            target_folder=ctx.obj.odoo_main_path,
            repo_spec_file=repospec_yml,
            force_fetch=force_fetch,
            add_compare_comment=add_compare_comments,
            download_archive=ctx.obj.source_download_archive,
        )

    if update_mode in ["all", "thirdparty"]:
        git_repos = git_clone_thirdparty_repos(
            root_folder=thirdparty_addon_path,
            git_yml_path=repospec_yml,
            generate_yml_compare_comments=add_compare_comments,
        )
    if remove_unspecified_addons:
        _git_remove_unused_folders(
            thirdparty_addon_path=thirdparty_addon_path,
            thirdparty_repos=git_repos.get("thirdparty", []),
            keep_folders=[zip_addon_path],
        )

    if (conf_path := ctx.obj.odoo_conf_path).exists():
        odoo_addon_paths = [ctx.obj.odoo_main_path / "addons"]
        if get_odoo_addons_in_folder(ctx.obj.workspace_addon_path):
            odoo_addon_paths.append(ctx.obj.workspace_addon_path)
        if get_odoo_addons_in_folder(zip_addon_path):
            odoo_addon_paths.append(zip_addon_path)
        odoo_addon_paths += [
            p for p in thirdparty_addon_path.iterdir() if p.is_dir() and not p.resolve() == zip_addon_path.resolve()
        ]
        update_odoo_conf_addon_paths(odoo_conf=conf_path, addon_paths=odoo_addon_paths)
