"""Common CLI functionality and configuration classes.

This module provides shared functionality for the gOdoo CLI, including:
- Path configuration for Odoo and addon directories
- Database connection settings
- RPC configuration
- Launch parameters and options
- Source code management settings

The classes in this module serve as configuration containers and are used
throughout the CLI to maintain consistent settings and defaults.
"""

from dataclasses import dataclass

from typer import Argument, Option
from typer_common_functions import typer_retuner, typer_unpacker


@dataclass
class OdooLaunchArgs:
    """Common Args for Odoo Launch Process."""

    extra_cmd_args = Option(help="extra agruments to pass to odoo-bin", envvar="ODOO_BIN_ARGS", rich_help_panel="Odoo")
    extra_cmd_args_bootstrap = Option(
        help="extra agruments to pass to odoo-bin in bootstrap mode",
        envvar="ODOO_BIN_BOOTSTRAP_ARGS",
        rich_help_panel="Odoo",
    )
    multithread_worker_count = Option(
        help="count of worker threads. will enable proxy_mode if >0. (Autodetect with -1)",
        rich_help_panel="Odoo",
        envvar="ODOO_WORKER_COUNT",
    )
    languages = Option(
        help="languages to load by default",
        rich_help_panel="Odoo",
        envvar="ODOO_LAUNCH_LANGUAGES",
    )
    install_workspace_modules = Option(
        help="Automatically install modules found in [bold cyan]--workspace_path[/bold cyan]",
        rich_help_panel="Odoo",
    )
    odoo_demo = Option("--odoo-demo", help="Load Demo Data", rich_help_panel="Odoo")
    dev_mode = Option(
        "--dev-mode",
        help="Passes '[bold cyan]--dev xml,qweb,reload[/bold cyan]' to odoo",
        rich_help_panel="Odoo",
    )
    log_file_path = Option(dir_okay=False, writable=True, help="Logfile Path", rich_help_panel="Odoo")
    banner_text = Argument(
        help="Banner Text to add to Odoo Log",
        envvar="ODOO_BANNER_TEXT",
    )
    banner_bg_color = Option(
        help="Banner Background Color",
        envvar="ODOO_BANNER_BG_COLOR",
    )


@dataclass
class OdooPathCLIArgs:
    """Common Args for Odoo Paths."""

    bin_path = Option(
        envvar="ODOO_MAIN_FOLDER",
        help="folder that contains odoo-bin",
        rich_help_panel="Path Options",
    )

    conf_path = Option(
        envvar="ODOO_CONF_PATH",
        help="odoo.conf path",
        rich_help_panel="Path Options",
    )

    workspace_addon_path = Option(
        envvar="ODOO_WORKSPACE_ADDON_LOCATION",
        help="path to dev workspace addons",
        rich_help_panel="Path Options",
    )
    thirdparty_addon_path = Option(
        envvar="ODOO_THIRDPARTY_LOCATION",
        help="folder that contains thirdparty repos like OCA",
        rich_help_panel="Path Options",
    )


@dataclass
class RpcCLIArgs:
    """Common Args for Odoo RPC."""

    rpc_host = Option(
        envvar="ODOO_RPC_HOST",
        help="Odoo RPC Host",
        rich_help_panel="RPC Options",
    )

    rpc_user = Option(
        envvar="ODOO_RPC_USER",
        help="User for RPC login",
        rich_help_panel="RPC Options",
    )

    rpc_password = Option(
        envvar="ODOO_RPC_PASSWORD",
        help="Password RPC Login Password",
        rich_help_panel="RPC Options",
    )
    rpc_db_name = Option(
        envvar=["ODOO_RPC_DB_NAME", "ODOO_MAIN_DB"],
        help="RPC database name",
        rich_help_panel="RPC Options",
    )


@dataclass
class DatabaseCLIArgs:
    """Common Args for Odoo Database."""

    db_filter = Option(
        envvar="ODOO_DB_FILTER",
        help="database filter for odoo_conf",
        rich_help_panel="Database Options",
    )
    db_host = Option(
        envvar="ODOO_DB_HOST",
        help="db hostname (empty for default socket)",
        rich_help_panel="Database Options",
    )
    db_name = Option(
        envvar="ODOO_MAIN_DB",
        help="launch database name",
        rich_help_panel="Database Options",
    )
    db_user = Option(
        envvar="ODOO_DB_USER",
        help="db user",
        rich_help_panel="Database Options",
    )
    db_password = Option(
        envvar="ODOO_DB_PASSWORD",
        help="db password",
        rich_help_panel="Database Options",
    )
    db_port = Option(
        envvar="ODOO_DB_PORT",
        help="db host port (empty for socket)",
        rich_help_panel="Database Options",
    )


@dataclass
class SourceGetArgs:
    """Common Args for Source Code Management."""

    mainfest_path = Option(
        envvar="ODOO_MANIFEST",
        help="godoo manifest path, when downloading odoo source (skip repo_url)",
    )

    source_download_archive = Option(
        "--source-download-archive",
        envvar="SOURCE_CLONE_ARCHIVE",
        help="When using a HTTPs Repo Url for Github we can download a snapshop without the Repo history",
    )


class CommonCLI:
    """Common CLI Class.

    Helps provide Default arguments for Typer
    """

    def __init__(self) -> None:
        """Initialize CommonCLI with default arguments."""
        self.odoo_paths = OdooPathCLIArgs
        self.odoo_launch = OdooLaunchArgs
        self.database = DatabaseCLIArgs
        self.rpc = RpcCLIArgs
        self.source = SourceGetArgs
        self.returner = typer_retuner
        self.unpacker = typer_unpacker
