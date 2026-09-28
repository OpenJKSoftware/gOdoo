"""Run interactive or scripted Odoo shell sessions."""

import logging
import sys
from pathlib import Path
from typing import Annotated

import typer

from ...runtime.odoo import build_odoo_shell_command, run_odoo_command
from ..common import CommonCLI
from ..configuration import require_cli_odoo_version, resolve_command_config

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)
SHELL_SCRIPTS_PATH = Path(__file__).parent / "shell_scripts"


def complete_script_name() -> list[str]:
    """Return available internal shell scripts for command completion."""
    return sorted(path.stem for path in SHELL_SCRIPTS_PATH.glob("*.py") if path.stem != "__init__")


def validate_script_name(script_name: str) -> str:
    """Validate that a requested script belongs to the bundled shell scripts."""
    available_scripts = complete_script_name()
    if script_name not in available_scripts:
        available_names = ", ".join(available_scripts) if available_scripts else "none"
        message = f"Unknown script '{script_name}'. Available scripts: {available_names}"
        raise typer.BadParameter(message)
    return script_name


def script_name_argument() -> object:
    """Build the shell-script argument with completion and validation."""
    available_scripts = complete_script_name()
    available_names = ", ".join(available_scripts) if available_scripts else "none"
    return typer.Argument(
        help=f"Bundled shell script to run. Available scripts: {available_names}",
        autocompletion=complete_script_name,
        callback=validate_script_name,
    )


def _shell_command(
    *,
    odoo_main_path: Path,
    odoo_conf_path: Path | None,
    db_name: str | None,
    db_user: str | None,
    db_host: str | None,
    db_port: int | None,
    db_password: str | None,
    data_dir: Path | None,
    addon_paths: list[Path] | None,
) -> list[str] | None:
    """Resolve and build an Odoo shell command through the runtime domain."""
    config_path = odoo_conf_path or odoo_main_path / "odoo.conf"
    if not config_path.exists() and (db_name is None or db_user is None):
        LOGGER.error("A database name and user are required when no Odoo config file is available.")
        return None
    config = resolve_command_config(
        odoo_main_path=odoo_main_path,
        odoo_conf_path=config_path,
        workspace_addon_path=(addon_paths or [odoo_main_path])[0],
        data_dir=data_dir,
        db_name=db_name,
        db_user=db_user,
        db_host=db_host,
        db_port=db_port,
        db_password=db_password,
        extra={"resolved_addon_paths": tuple(addon_paths)} if addon_paths else None,
    )
    require_cli_odoo_version(config.odoo_install_folder, ">=16,<20")
    return build_odoo_shell_command(config)


def odoo_shell(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Path | None, CLI.odoo_paths.conf_path] = None,
    db_name: Annotated[str | None, CLI.database.db_name] = None,
    db_user: Annotated[str | None, CLI.database.db_user] = None,
    db_host: Annotated[str | None, CLI.database.db_host] = None,
    db_port: Annotated[int | None, CLI.database.db_port] = None,
    db_password: Annotated[str | None, CLI.database.db_password] = None,
    data_dir: Annotated[Path | None, CLI.odoo_paths.data_dir] = None,
    addon_paths: Annotated[
        list[Path] | None,
        typer.Option(
            "--addon-path",
            envvar="ODOO_ADDON_PATHS",
            help="Addon path(s) to use when no Odoo config file exists; repeat for multiple paths.",
        ),
    ] = None,
    pipe_in_command: Annotated[
        str,
        typer.Argument(help="Python command, that will be piped into odoo-bin shell"),
    ] = "",
):
    """Run an interactive Odoo shell or pipe Python code into it."""
    shell_cmd = _shell_command(
        odoo_main_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        db_name=db_name,
        db_user=db_user,
        db_host=db_host,
        db_port=db_port,
        db_password=db_password,
        data_dir=data_dir,
        addon_paths=addon_paths,
    )
    if shell_cmd is None:
        return CLI.returner(1)

    if pipe_in_command:
        ret = run_odoo_command(shell_cmd, input=pipe_in_command, text=True)
    else:
        ret = run_odoo_command(shell_cmd, stdin=sys.stdin)
    return CLI.returner(ret.returncode)


def odoo_shell_run_script(
    script_name: Annotated[str, script_name_argument()],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Path | None, CLI.odoo_paths.conf_path] = None,
    db_name: Annotated[str | None, CLI.database.db_name] = None,
    db_user: Annotated[str | None, CLI.database.db_user] = None,
    script_args: Annotated[list[str] | None, typer.Argument(help="Arguments to pass to the script")] = None,
    db_host: Annotated[str | None, CLI.database.db_host] = None,
    db_port: Annotated[int | None, CLI.database.db_port] = None,
    db_password: Annotated[str | None, CLI.database.db_password] = None,
    data_dir: Annotated[Path | None, CLI.odoo_paths.data_dir] = None,
    addon_paths: Annotated[
        list[Path] | None,
        typer.Option(
            "--addon-path",
            envvar="ODOO_ADDON_PATHS",
            help="Addon path to use when no Odoo config file exists; repeat for multiple paths.",
        ),
    ] = None,
):
    """Run a bundled Python script in an Odoo shell session."""
    script_path = SHELL_SCRIPTS_PATH / f"{script_name}.py"
    if not script_path.is_file():
        LOGGER.error("Shell script '%s' is unavailable.", script_name)
        return CLI.returner(1)
    shell_cmd = _shell_command(
        odoo_main_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        db_name=db_name,
        db_user=db_user,
        db_host=db_host,
        db_port=db_port,
        db_password=db_password,
        data_dir=data_dir,
        addon_paths=addon_paths,
    )
    if shell_cmd is None:
        return CLI.returner(1)
    script_with_args = f"script_args = {list(script_args or [])!r}\n\n{script_path.read_text()}"
    LOGGER.info("Running bundled shell script %s", script_name)
    result = run_odoo_command(shell_cmd, input=script_with_args, text=True)
    return CLI.returner(result.returncode)
