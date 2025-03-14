"""Commands to clone Odoo and addon source code."""

import configparser
import logging
import shutil
import tempfile
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import typer
from ruamel.yaml import YAML

from ..cli_common import CommonCLI
from ..git import GitUrl, git_ensure_addon_repos, git_ensure_odoo_repo
from ..helpers.modules import GodooModules, get_addon_paths, get_zip_addon_path
from ..helpers.modules_py import _install_py_reqs_for_modules
from ..helpers.odoo_manifest import remove_unused_folders
from ..helpers.system import download_file
from .db.connection import DBConnection
from .db.query import _get_installed_modules

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


class UpdateMode(str, Enum):
    """Update mode enumeration for source code management.

    This enum defines the available update modes for source code:
    - all: Update all sources (Odoo, third-party, and zip archives)
    - zip: Update only zip archives
    - odoo: Update only the Odoo source code
    - thirdparty: Update only third-party addons
    """

    all = "all"
    zip = "zip"
    odoo = "odoo"
    thirdparty = "thirdparty"


def unpack_addon_archives(
    archive_folder: Path,
    target_addon_folder: Path,
    remove_excess: bool = False,
):
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
    target_addon_folder.mkdir(exist_ok=True, parents=True)
    if remove_excess:
        LOGGER.debug("Clearing out unarchive folder: %s", target_addon_folder)
        for folder in target_addon_folder.iterdir():
            shutil.rmtree(folder)
    LOGGER.info("Extracting archive addons to: %s", target_addon_folder)
    for zip_file in archive_folder.glob("*.zip"):
        LOGGER.info("Extracting addon archive: %s", zip_file)
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            shutil.unpack_archive(zip_file, td)
            # We can have zip files with one or more modules.
            # Either the first folder contains multiple or its a module by itself
            # So first get the real modules form the zip root or one level down and then move them to subpaths
            possible_paths = [td, *list(td.glob("*/"))]
            zip_modules = list(GodooModules(possible_paths).get_modules())
            if not zip_modules:
                LOGGER.warning("Could not find valid modules in thirdparty zip: %s", zip_file)
                continue
            LOGGER.debug(
                "Found modules in Zipfile:\n%s",
                [str(f.path.relative_to(td)) for f in zip_modules],
            )
            target_folder = target_addon_folder / ("single_mods" if len(zip_modules) == 1 else zip_file.stem)
            target_folder.mkdir(exist_ok=True)
            for m in zip_modules:
                module_target = target_folder / m.name
                shutil.rmtree(module_target, ignore_errors=True)
                shutil.move(m.path, module_target)


def update_odoo_conf_addon_paths(odoo_conf: Path, addon_paths: list[Path]):
    """Update Odoo.Conf with Addon Paths.

    Parameters
    ----------
    odoo_conf : Path
        odoo.conf location
    addon_paths : List[Path]
        list of paths
    """
    if not odoo_conf.exists():
        msg = f"Odoo.conf not found at: {odoo_conf!s}"
        LOGGER.error(msg)
        raise FileNotFoundError(msg)
    config = configparser.ConfigParser()
    config.read(odoo_conf)
    path_strings = [str(p.absolute()) for p in addon_paths]
    addon_path_option = ",".join(path_strings)
    config["options"]["addons_path"] = addon_path_option
    LOGGER.info("Writing Addon Paths to Odoo Config.")
    LOGGER.debug(addon_path_option)
    config.write(odoo_conf.open("w"))


def py_depends_by_db(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
):
    """Install Python dependencies for all installed modules in DB.

    Will not raise error if module not found in source for upgrade purposes.
    """
    connection = DBConnection(hostname=db_host, port=db_port, username=db_user, password=db_password, db_name=db_name)
    module_list = _get_installed_modules(connection, to_install=True)
    if isinstance(module_list, int):
        return CLI.returner(module_list)
    LOGGER.info("Ensuring Py Reqiurements for Installed Modules are met")
    LOGGER.debug("Modules:\n%s", module_list)
    odoo_addon_paths = get_addon_paths(
        odoo_main_repo=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )
    module_list = list(module_list)
    module_reg = GodooModules(odoo_addon_paths)
    modules = list(module_reg.get_modules(module_list, raise_missing_names=False))
    _install_py_reqs_for_modules(modules, module_reg)


def get_installed_module_paths(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
):
    """Get Paths of all installed modules in DB."""
    connection = DBConnection(hostname=db_host, port=db_port, username=db_user, password=db_password, db_name=db_name)
    module_list = _get_installed_modules(connection)
    if isinstance(module_list, int):
        return CLI.returner(module_list)
    LOGGER.debug("Searching Folders for:\n%s", module_list)
    odoo_addon_paths = get_addon_paths(
        odoo_main_repo=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )
    module_list = list(module_list)
    modules = GodooModules(odoo_addon_paths).get_modules(module_list)
    for m in modules:
        print(m.path.absolute())  # pylint: disable=print-used


def py_depends_by_modules(
    module_list: Annotated[
        list[str],
        typer.Argument(help="Modules to check for dependencies (can use all for all available addons)"),
    ],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
):
    """Install dependencies from __manifest__.py in specified modules."""
    odoo_addon_paths = get_addon_paths(
        odoo_main_repo=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )

    if len(module_list) == 1 and module_list[0] == "all":
        module_list = []
    module_reg = GodooModules(odoo_addon_paths)
    modules = list(module_reg.get_modules(module_list))
    _install_py_reqs_for_modules(modules, module_reg)


def get_source_file(
    save_path: Annotated[Path, typer.Option(file_okay=True, dir_okay=False, help="Where to write the file")],
    manifest_path: Annotated[Optional[Path], CLI.source.mainfest_path] = None,
    file_path: Annotated[str, typer.Option(help="Relative Filepath in Repository")] = "",
    repo_url: Annotated[str, typer.Option(help="git repo url, for specific repo (skip manifest_yml)")] = "",
    file_ref: Annotated[str, typer.Option(help="When not using manifest. File Branch, Commit, Tag...")] = "",
):
    """Get Raw file from manifest git remotes or specific git remote."""
    if not repo_url and not manifest_path:
        msg = "Need to provide either manifest_yml or repo_url"
        LOGGER.error(msg)
        raise ValueError(msg)
    if manifest_path and not repo_url:
        manifest = YAML().load(manifest_path.resolve())
        odoo_spec = manifest["odoo"]
        repo_url = odoo_spec["url"]
        file_ref = odoo_spec.get("commit") or odoo_spec.get("branch")
    if not file_ref:
        msg = "Need to provide file ref. If you provided a manifest, make sure there is a branch or commit key in the odoo section"
        LOGGER.error(msg)
        raise ValueError(msg)
    git_url = GitUrl(repo_url)
    file_url = git_url.get_file_raw_url(ref=file_ref, file_path=file_path)

    return download_file(url=file_url, save_path=save_path)


def get_source(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    thirdparty_zip_source: Annotated[
        Path, typer.Option(envvar="ODOO_THIRDPARTY_ZIP_LOCATION", help="Source folder, where to look for Addon zips")
    ],
    update_mode: Annotated[UpdateMode, typer.Argument(help="What to Update")] = UpdateMode.all,
    manifest_path: Annotated[Optional[Path], CLI.source.mainfest_path] = None,
    download_zipmode: Annotated[bool, CLI.source.source_download_archive] = False,
    add_compare_comments: Annotated[
        bool,
        typer.Option("--add-compare-comments", help="Wether to add github.com three dot compare links as comments."),
    ] = False,
    pin_commits: Annotated[
        bool, typer.Option("--pin-commits", help="Pin commits in manifest to current commit in repo")
    ] = False,
    remove_unspecified_addons: Annotated[
        bool,
        typer.Option("--remove-unspecified-addons", help="Remove Addon folders that are not in YML or thirdparty.zip"),
    ] = False,
    force_fetch: Annotated[
        bool,
        typer.Option(
            "--force-fetch",
            help="Forces origin fetch, regardless of current branch or commit sha (may be slow)",
        ),
    ] = False,
):
    """Download/unzip Odoo source and thirdparty addons."""
    LOGGER.info("Updating Source Repos")
    zip_addon_path = get_zip_addon_path(thirdparty_addon_path)

    if update_mode in ["all", "zip"]:
        unpack_addon_archives(thirdparty_zip_source, zip_addon_path, remove_excess=remove_unspecified_addons)

    if update_mode in ["all", "odoo"]:
        git_ensure_odoo_repo(
            target_folder=odoo_main_path,
            manifest_file=manifest_path,
            force_fetch=force_fetch,
            add_compare_comment=add_compare_comments,
            download_archive=download_zipmode,
            pin_commit=pin_commits,
        )

    if update_mode in ["all", "thirdparty"]:
        git_repos = git_ensure_addon_repos(
            root_folder=thirdparty_addon_path,
            git_yml_path=manifest_path,
            generate_yml_compare_comments=add_compare_comments,
            download_archive=download_zipmode,
            pin_commits=pin_commits,
        )
        if remove_unspecified_addons:
            remove_unused_folders(
                thirdparty_addon_path=thirdparty_addon_path,
                thirdparty_repos=git_repos.get("thirdparty", []),
                keep_folders=[zip_addon_path],
            )

    if (conf_path := odoo_conf_path).exists():
        odoo_addon_paths = get_addon_paths(
            odoo_main_repo=odoo_main_path,
            workspace_addon_path=workspace_addon_path,
            thirdparty_addon_path=thirdparty_addon_path,
        )
        update_odoo_conf_addon_paths(odoo_conf=conf_path, addon_paths=odoo_addon_paths)


def update_odoo_conf(
    odoo_conf: Annotated[Path, CLI.odoo_paths.conf_path],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
):
    """Update Odoo.conf with Addon Paths."""
    odoo_addon_paths = get_addon_paths(
        odoo_main_repo=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )
    update_odoo_conf_addon_paths(odoo_conf=odoo_conf, addon_paths=odoo_addon_paths)


def source_cli_app():
    """Create and configure the source CLI application.

    This function sets up the command-line interface for source code management,
    including commands for getting and updating source code, managing addon paths,
    and handling dependencies.

    Returns:
        typer.Typer: The configured CLI application instance.
    """
    app = typer.Typer(
        no_args_is_help=True,
        help="Functions concerning with Odoo Source code",
    )

    app.command("get")(get_source)
    app.command("sync-conf")(update_odoo_conf)
    app.command("get-file")(get_source_file)
    app.command("get-dependencies")(py_depends_by_modules)
    app.command("get-dependencies-db")(py_depends_by_db)
    app.command("installed-module-paths")(get_installed_module_paths)

    return app
