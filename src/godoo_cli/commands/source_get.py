import configparser
import logging
import shutil
import zipfile
from enum import Enum
from pathlib import Path
from typing import List

import typer
from ruamel.yaml import YAML

from ..git import GitUrl, git_ensure_addon_repos, git_ensure_odoo_repo
from ..helpers import download_file
from ..helpers.cli import typer_unpacker
from ..helpers.odoo_files import get_addon_paths
from ..helpers.odoo_manifest import remove_unused_folders

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
def get_source_file(
    manifest_yml: Path = typer.Option(
        "", envvar="ODOO_MANIFEST", help="godoo manifest path, when downloading odoo source (skip repo_url)"
    ),
    repo_url: str = typer.Option("", help="git repo url, for specific repo (skip manifest_yml)"),
    file_ref: str = typer.Option("", help="When not using manifest. File Branch, Commit, Tag..."),
    file_path: str = typer.Option(..., help="Relative Filepath in Repository"),
    save_path: Path = typer.Option(..., file_okay=True, dir_okay=False, help="Where to write the file"),
):
    """Get Raw file from git remote.
    Either from the Odoo Repo defined in manifest file or from a custom Git remote.
    """

    if not repo_url and not manifest_yml:
        raise ValueError("Need to provide either manifest_yml or repo_url")
    if manifest_yml and not repo_url:
        manifest = YAML().load(manifest_yml.resolve())
        odoo_spec = manifest["odoo"]
        repo_url = odoo_spec["url"]
        file_ref = odoo_spec.get("commit") or odoo_spec.get("branch")
    if not file_ref:
        raise ValueError(
            "Need to provide file ref. If you provided a manifest, make sure there is a branch or commit key in the odoo section"
        )
    git_url = GitUrl(repo_url)
    file_url = git_url.get_file_raw_url(ref=file_ref, file_path=file_path)

    return download_file(url=file_url, save_path=save_path)


@typer_unpacker
def get_source(
    ctx: typer.Context,
    manifest_yml: Path = typer.Option(
        ..., envvar="ODOO_MANIFEST", help="Git.yml file, that specified what to download with wich prefix"
    ),
    thirdparty_addon_path: Path = typer.Option(
        ..., envvar="ODOO_THIRDPARTY_LOCATION", help="Root folder of the Thirdparty addon repos"
    ),
    thirdparty_zip_source: Path = typer.Option(
        ..., envvar="ODOO_THIRDPARTY_ZIP_LOCATION", help="Source folder, where to look for Addon zips"
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
    """Update Odoo Source and Thirdparty source."""
    LOGGER.info("Updating Source Repos")
    zip_addon_path = thirdparty_addon_path / "custom"  # !Todo Pull out into variable (same in bootstrap)

    if update_mode in ["all", "zip"]:
        unzip_addons(thirdparty_zip_source, zip_addon_path)

    if update_mode in ["all", "odoo"]:
        git_ensure_odoo_repo(
            target_folder=ctx.obj.odoo_main_path,
            manifest_file=manifest_yml,
            force_fetch=force_fetch,
            add_compare_comment=add_compare_comments,
            download_archive=ctx.obj.source_download_archive,
        )

    if update_mode in ["all", "thirdparty"]:
        git_repos = git_ensure_addon_repos(
            root_folder=thirdparty_addon_path,
            git_yml_path=manifest_yml,
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
        odoo_addon_paths = get_addon_paths(
            odoo_main_repo=ctx.obj.odoo_main_path,
            workspace_addon_path=ctx.obj.workspace_addon_path,
            zip_addon_path=zip_addon_path,
            thirdparty_addon_path=thirdparty_addon_path,
        )
        update_odoo_conf_addon_paths(odoo_conf=conf_path, addon_paths=odoo_addon_paths)
