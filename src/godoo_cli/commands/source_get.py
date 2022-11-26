"""Commands to clone Odoo and addon source code"""
import configparser
import logging
import shutil
import tempfile
from enum import Enum
from pathlib import Path
from typing import List

import typer
from ruamel.yaml import YAML

from ..git import GitUrl, git_ensure_addon_repos, git_ensure_odoo_repo
from ..helpers.bootstrap import _install_py_reqs_for_modules
from ..helpers.cli import typer_unpacker
from ..helpers.odoo_files import get_addon_paths, get_odoo_module_paths, get_zip_addon_path
from ..helpers.odoo_manifest import remove_unused_folders
from ..helpers.system import download_file

LOGGER = logging.getLogger(__name__)


class UpdateMode(str, Enum):
    all = "all"
    zip = "zip"
    odoo = "odoo"
    thirdparty = "thirdparty"


def unpack_addon_archives(archive_folder: Path, target_addon_folder: Path, remove_excess: bool = False):
    """Take archive files from archive_folder and extract them into target_addon_folder.

    Parameters
    ----------
    archive_folder : Path
        Where to look for zip files
    target_addon_folder : Path
        where to place them
    remove_excess : bool , optional
        remove all and then unzip, by default False
    """
    if remove_excess:
        LOGGER.debug("Clearing out unarchive folder: %s", target_addon_folder)
        for folder in target_addon_folder.iterdir():
            shutil.rmtree(folder)
    LOGGER.info("Extracting archive addons to: %s", target_addon_folder)
    for zip_file in archive_folder.glob("*.zip"):
        target_addon_folder.mkdir(exist_ok=True, parents=True)
        LOGGER.info("Extracting addon archive: %s", zip_file)
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            shutil.unpack_archive(zip_file, td)
            # We can have zip files with one or more modules.
            # Either the first folder contains multiple or its a module by itself
            # So first get the real modules form the zip root or one level down and then move them to subpaths
            zip_module_paths = get_odoo_module_paths([td] + list(td.glob("*/")))
            if not zip_module_paths:
                LOGGER.warning("Could not find valid modules in thirdparty zip: %s", zip_file)
                continue
            LOGGER.debug(
                "Found modules in Zipfile:\n%s",
                "\n".join([str(f.relative_to(td)) for f in zip_module_paths]),
            )
            target_folder = target_addon_folder / ("single_mods" if len(zip_module_paths) == 1 else zip_file.stem)
            target_folder.mkdir(exist_ok=True)
            for m in zip_module_paths:
                module_target = target_folder / m.stem
                shutil.rmtree(module_target, ignore_errors=True)
                shutil.move(m, module_target)


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
def install_module_dependencies(
    ctx: typer.Context,
    thirdparty_addon_path: Path = typer.Option(
        ...,
        envvar="ODOO_THIRDPARTY_LOCATION",
        help="Root folder of the Thirdparty addon repos",
    ),
    module_list: List[str] = typer.Argument(
        ...,
        help="Modules to check for dependencies (can use all for all available addons)",
    ),
):
    odoo_addon_paths = get_addon_paths(
        odoo_main_repo=ctx.obj.odoo_main_path,
        workspace_addon_path=ctx.obj.workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )
    if len(module_list) == 1 and module_list[0] == "all":
        search_addon_paths = [p for p in odoo_addon_paths if ctx.obj.odoo_main_path not in p.parents]
        module_list = [p.stem for p in get_odoo_module_paths(search_addon_paths)]
    _install_py_reqs_for_modules(odoo_addon_paths, module_list)


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
    update_mode: UpdateMode = typer.Argument(UpdateMode.all, help="What to Update"),
    manifest_yml: Path = typer.Option(
        ..., envvar="ODOO_MANIFEST", help="Git.yml file, that specified what to download with wich prefix"
    ),
    thirdparty_addon_path: Path = typer.Option(
        ..., envvar="ODOO_THIRDPARTY_LOCATION", help="Root folder of the Thirdparty addon repos"
    ),
    thirdparty_zip_source: Path = typer.Option(
        ..., envvar="ODOO_THIRDPARTY_ZIP_LOCATION", help="Source folder, where to look for Addon zips"
    ),
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
    zip_addon_path = get_zip_addon_path(thirdparty_addon_path)

    if update_mode in ["all", "zip"]:
        unpack_addon_archives(thirdparty_zip_source, zip_addon_path, remove_excess=remove_unspecified_addons)

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
            thirdparty_addon_path=thirdparty_addon_path,
        )
        update_odoo_conf_addon_paths(odoo_conf=conf_path, addon_paths=odoo_addon_paths)
