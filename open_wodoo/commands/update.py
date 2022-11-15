import configparser
import logging
import shutil
import zipfile
from enum import Enum
from pathlib import Path
from typing import List

import typer

from ..git import git_ensure_addon_repos, git_ensure_odoo_repo
from ..helpers.cli import typer_unpacker
from ..helpers.odoo_files import get_odoo_addons_in_folder
from ..helpers.repospec import remove_unused_folders

LOGGER = logging.getLogger(__name__)


class UpdateMode(str, Enum):
    all = "all"
    zip = "zip"
    odoo = "odoo"
    thirdparty = "thirdparty"


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
        git_ensure_odoo_repo(
            target_folder=ctx.obj.odoo_main_path,
            repo_spec_file=repospec_yml,
            force_fetch=force_fetch,
            add_compare_comment=add_compare_comments,
            download_archive=ctx.obj.source_download_archive,
        )

    if update_mode in ["all", "thirdparty"]:
        git_repos = git_ensure_addon_repos(
            root_folder=thirdparty_addon_path,
            git_yml_path=repospec_yml,
            generate_yml_compare_comments=add_compare_comments,
            download_archive=ctx.obj.source_download_archive,
        )
    if remove_unspecified_addons:
        remove_unused_folders(
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
