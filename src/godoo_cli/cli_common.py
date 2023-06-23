from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from typer import Option
from typer_common_functions import get_type_from_default, typer_retuner, typer_unpacker


@dataclass
class OdooLaunchArgs:
    extra_cmd_args: List[str] = Option(None, help="extra agruments to pass to odoo-bin", rich_help_panel="Odoo")
    multithread_worker_count: int = Option(
        -1, help="count of worker threads. will enable proxy_mode if >0. (Autodetect with -1)", rich_help_panel="Odoo"
    )
    languages: str = Option("de_DE,en_US", help="languages to load by default", rich_help_panel="Odoo")
    no_install_base: bool = Option(
        False,
        "--no-install-base",
        help="dont install [bold]base[/bold] and [bold]web[/bold] module",
        rich_help_panel="Odoo",
    )
    no_install_workspace_modules: bool = Option(
        False,
        "--no-install-workspace-modules",
        help="dont automatically install modules found in [bold cyan]--workspace_path[/bold cyan]",
        rich_help_panel="Odoo",
    )
    no_update_source: bool = Option(
        False, "--no-update-source", help="Update Odoo Source and Thirdparty Addons", rich_help_panel="Source Code"
    )
    no_addons_remove_unspecified: bool = Option(
        False,
        "--no-addons-remove-unspecified",
        help="don't remove unspecified addons if not '[bold cyan]--no-update-source[/bold cyan]'",
        rich_help_panel="Source Code",
    )
    odoo_demo: bool = Option(False, "--odoo-demo", help="Load Demo Data", rich_help_panel="Odoo")
    dev_mode: bool = Option(
        False,
        "--dev-mode",
        help="Passes '[bold cyan]--dev xml,qweb,reload[/bold cyan]' to odoo",
        rich_help_panel="Odoo",
    )
    log_file_path: Path = Option(None, dir_okay=False, writable=True, help="Logfile Path", rich_help_panel="Odoo")


@dataclass
class OdooPathCLIArgs:
    bin_path: Path = Option(
        ...,
        envvar="ODOO_MAIN_FOLDER",
        help="folder that contains odoo-bin",
        rich_help_panel="Path Options",
    )

    conf_path: Path = Option(
        ...,
        envvar="ODOO_CONF_PATH",
        help="odoo.conf path",
        rich_help_panel="Path Options",
    )

    workspace_addon_path: Path = Option(
        ...,
        envvar="ODOO_WORKSPACE_ADDON_LOCATION",
        help="path to dev workspace addons",
        rich_help_panel="Path Options",
    )
    thirdparty_addon_path: Path = Option(
        ...,
        envvar="ODOO_THIRDPARTY_LOCATION",
        help="folder that contains thirdparty repos like OCA",
        rich_help_panel="Path Options",
    )


@dataclass
class RpcCLIArgs:
    rpc_host: str = Option(
        ...,
        envvar="ODOO_RPC_HOST",
        help="Odoo RPC Host",
        rich_help_panel="RPC Options",
    )

    rpc_user: str = Option(
        ...,
        envvar="ODOO_RPC_USER",
        help="User for RPC login",
        rich_help_panel="RPC Options",
    )

    rpc_password: str = Option(
        ...,
        envvar="ODOO_RPC_PASSWORD",
        help="Password RPC Login Password",
        rich_help_panel="RPC Options",
    )
    rpc_db_name: str = Option(
        ...,
        envvar=["ODOO_RPC_DB_NAME", "ODOO_MAIN_DB"],
        help="RPC database name",
        rich_help_panel="RPC Options",
    )


@dataclass
class DatabaseCLIArgs:
    db_filter: str = Option(
        ...,
        envvar="ODOO_DB_FILTER",
        help="database filter for odoo_conf",
        rich_help_panel="Database Options",
    )
    db_host: str = Option(
        "",
        envvar="ODOO_DB_HOST",
        help="db hostname (empty for default socket)",
        rich_help_panel="Database Options",
    )
    db_name: str = Option(
        ...,
        envvar="ODOO_MAIN_DB",
        help="launch database name",
        rich_help_panel="Database Options",
    )
    db_user: str = Option(
        ...,
        envvar="ODOO_DB_USER",
        help="db user",
        rich_help_panel="Database Options",
    )
    db_password: str = Option(
        ...,
        envvar="ODOO_DB_PASSWORD",
        help="db password",
        rich_help_panel="Database Options",
    )
    db_port: str = Option(
        "",
        envvar="ODOO_DB_PORT",
        help="db host port (empty for socket)",
        rich_help_panel="Database Options",
    )


@dataclass
class SourceGetArgs:
    mainfest_path: Path = Option(
        "",
        envvar="ODOO_MANIFEST",
        help="godoo manifest path, when downloading odoo source (skip repo_url)",
    )

    source_download_archive: Optional[bool] = Option(
        False,
        "--source-download-archive",
        envvar="SOURCE_CLONE_ARCHIVE",
        help="When using a HTTPs Repo Url for Github we can download a snapshop without the Repo history",
    )


class CommonCLI:
    def __init__(self) -> None:
        self.odoo_paths = OdooPathCLIArgs
        self.odoo_launch = OdooLaunchArgs
        self.database = DatabaseCLIArgs
        self.rpc = RpcCLIArgs
        self.source = SourceGetArgs
        self.returner = typer_retuner
        self.unpacker = typer_unpacker

    @property
    def arg_annotator(self):
        """Add type annotations for

        Returns
        -------
        Callback
            Annotator function with CommonCLI Props as arguments
        """
        my_args = self.__dict__
        return get_type_from_default(*list(my_args.values()))
