"""Commands to clone Odoo and addon source code."""

import logging
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import typer

from ..cli_common import CommonCLI
from ..git.git_odoo_addons import git_ensure_repo_matches_manifest, git_ensure_thirdparty_repos
from ..git.git_url import GitUrl
from ..helpers.modules_py import install_base_python_reqs, install_py_reqs_for_modules
from ..helpers.pip import pip_command
from ..helpers.system import download_file, run_cmd
from ..models import DBConnection, GodooConfig, GodooManifest, GodooModules
from .db.query import BOOTSTRAP_EXIT_CODE, DbBootstrapStatus, get_installed_modules_from_connection
from .source import unpack_addon_archives, update_odoo_conf_addon_paths

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
    module_list, status = get_installed_modules_from_connection(connection, to_install=True)
    if status != DbBootstrapStatus.BOOTSTRAPPED:
        return CLI.returner(BOOTSTRAP_EXIT_CODE[status])
    LOGGER.info("Ensuring Py Reqiurements for Installed Modules are met")
    LOGGER.debug("Modules:\n%s", module_list)
    godoo_config = GodooConfig(
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_filter=".*",
        odoo_install_folder=odoo_main_path,
        odoo_conf_path=Path("/tmp/odoo.conf"),
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )
    module_list = list(module_list)
    module_reg = GodooModules(godoo_config.addon_paths)
    modules = list(module_reg.get_modules(module_list, raise_missing_names=False))
    install_py_reqs_for_modules(modules, module_reg)


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
    godoo_config = GodooConfig(
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        odoo_install_folder=odoo_main_path,
        odoo_conf_path=Path("./odoo.conf"),
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )
    module_list, status = get_installed_modules_from_connection(godoo_config.db_connection)
    if status != DbBootstrapStatus.BOOTSTRAPPED:
        return CLI.returner(BOOTSTRAP_EXIT_CODE[status])
    LOGGER.debug("Searching Folders for:\n%s", module_list)
    module_list = list(module_list)
    modules = GodooModules(godoo_config.addon_paths).get_modules(module_list)
    for m in modules:
        print(m.path.absolute())  # pylint: disable=print-used


def py_depends_by_modules(
    module_list: Annotated[
        list[str],
        typer.Argument(
            help="Modules to check for dependencies (can use 'all' for all available addons, 'base' for just Odoo Base requirements.txt)"
        ),
    ],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
):
    """Install dependencies from __manifest__.py in specified modules."""
    godoo_config = GodooConfig(
        odoo_install_folder=odoo_main_path,
        odoo_conf_path=Path("./odoo.conf"),
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )

    if len(module_list) == 1:
        if module_list[0] == "all":
            module_list = []
        elif module_list[0] == "base":
            return install_base_python_reqs(odoo_install_folder=odoo_main_path)
    module_reg = GodooModules(godoo_config.addon_paths)
    modules = list(module_reg.get_modules(module_list))
    install_py_reqs_for_modules(modules, module_reg)


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
        manifest = GodooManifest.from_yaml_file(manifest_path)
        repo_url = manifest.odoo.url
        file_ref = manifest.odoo.ref
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
    godoo_config = GodooConfig(
        odoo_install_folder=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )
    update_zip = update_mode in ["all", "zip"]
    update_odoo = update_mode in ["all", "odoo"]
    update_thirdparty = update_mode in ["all", "thirdparty"]

    if not manifest_path:
        msg = "Manifest path is required when updating Odoo or third-party sources."
        LOGGER.error(msg)
        raise ValueError(msg)
    manifest = GodooManifest.from_yaml_file(manifest_path)

    if update_zip:
        unpack_addon_archives(
            thirdparty_zip_source, godoo_config.zip_addon_path, remove_excess=remove_unspecified_addons
        )

    if update_odoo:
        repo_spec = manifest.odoo if manifest else None
        if not repo_spec:
            msg = "Manifest is required to update Odoo source."
            LOGGER.error(msg)
            raise ValueError(msg)
        git_ensure_repo_matches_manifest(
            target_folder=godoo_config.odoo_install_folder,
            repo_spec=repo_spec,
            default_branch=manifest.default_branch,
            force_fetch=force_fetch,
            download_archive=download_zipmode,
            pin_commit=pin_commits,
        )
        odoo_bin = godoo_config.odoo_install_folder / "odoo-bin"
        if odoo_bin.exists():
            LOGGER.debug("chmod odoo-bin +executable")
            odoo_bin.chmod(0o755)
        else:
            LOGGER.warning("Could not find odoo-bin in %s", godoo_config.odoo_install_folder)
        LOGGER.info("Installing Odoo requirements")
        run_cmd(f"{pip_command()} install -r {godoo_config.odoo_install_folder / 'requirements.txt'}")

    if update_thirdparty:
        git_ensure_thirdparty_repos(
            root_folder=godoo_config.thirdparty_addon_path,
            manifest=manifest,
            generate_yml_compare_comments=add_compare_comments,
            download_archive=download_zipmode,
            pin_commits=pin_commits,
        )
        if remove_unspecified_addons:
            manifest.remove_unused_addon_folders(
                thirdparty_addon_path=godoo_config.thirdparty_addon_path,
                keep_folders=[godoo_config.zip_addon_path],
            )
    if add_compare_comments:
        manifest.to_yaml_file(add_compare_urls=add_compare_comments)

    if (conf_path := odoo_conf_path).exists():
        odoo_addon_paths = godoo_config.addon_paths
        update_odoo_conf_addon_paths(odoo_conf=conf_path, addon_paths=odoo_addon_paths)


def update_odoo_conf(
    odoo_conf: Annotated[Path, CLI.odoo_paths.conf_path],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
):
    """Update Odoo.conf with Addon Paths."""
    godoo_conf = GodooConfig(
        odoo_install_folder=odoo_main_path,
        odoo_conf_path=odoo_conf,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )
    update_odoo_conf_addon_paths(odoo_conf=odoo_conf, addon_paths=godoo_conf.addon_paths)


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
