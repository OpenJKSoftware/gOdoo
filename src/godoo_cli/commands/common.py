"""Define shared CLI options and environment defaults."""

import logging
from dataclasses import dataclass

from typer import Option
from typer_common_functions import typer_retuner

LOGGER = logging.getLogger(__name__)


class WorkspaceCLIArgs:
    """Reuse host-workspace options and their environment fallbacks."""

    manifest_path = Option("--manifest", envvar="ODOO_MANIFEST", help="Manifest path relative to the project root.")
    sources_root = Option(envvar="GODOO_SOURCES_ROOT", help="Absolute shared source root outside the project.")
    debug_port = Option(help="Loopback debugpy port.")
    json_output = Option("--json", help="Print machine-readable JSON.")


@dataclass
class OdooLaunchArgs:
    """Define shared Odoo process options."""

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
        envvar="GODOO_DEV_MODE",
    )
    log_file_path = Option(dir_okay=False, writable=True, help="Logfile Path", rich_help_panel="Odoo")


@dataclass
class OdooPathCLIArgs:
    """Define shared Odoo path options."""

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
    data_dir = Option(
        "--data-dir",
        envvar="ODOO_DATA_DIR",
        help="Odoo data directory, including filestores",
        rich_help_panel="Path Options",
    )


@dataclass
class RpcCLIArgs:
    """Define shared Odoo RPC options."""

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
    """Define shared PostgreSQL options."""

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
    db_template_name = Option(
        "--db-template",
        envvar="ODOO_DB_TEMPLATE",
        help="template database for an explicit reset",
        rich_help_panel="Database Options",
    )
    empty_reset = Option(
        "--empty",
        help="drop only selected runtime state; do not use a template",
        rich_help_panel="Lifecycle Options",
    )


class CommonCLI:
    """Collect reusable Typer option definitions."""

    def __init__(self) -> None:
        """Initialize CommonCLI with default arguments."""
        self.odoo_paths = OdooPathCLIArgs
        self.odoo_launch = OdooLaunchArgs
        self.database = DatabaseCLIArgs
        self.rpc = RpcCLIArgs
        self.returner = typer_retuner
