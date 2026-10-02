"""Typer adapter for runtime reset."""

from pathlib import Path
from typing import Annotated

from ...database.connection import DBConnection
from ...runtime.odoo import SUPPORTED_ODOO_VERSION_SPECIFIER
from ...runtime.reset import reset_empty_runtime, reset_runtime_from_template
from ..common import CommonCLI
from ..configuration import require_cli_odoo_version, resolve_development_odoo_main_path

CLI = CommonCLI()


def reset_odoo_state(
    db_name: Annotated[str, CLI.database.db_name],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Path | None, CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    db_template_name: Annotated[str, CLI.database.db_template_name] = "",
    empty_reset: Annotated[bool, CLI.database.empty_reset] = False,
) -> int:
    """Drop a runtime or replace it from its template through staging."""
    odoo_main_path = resolve_development_odoo_main_path(odoo_main_path)
    version = require_cli_odoo_version(odoo_main_path, SUPPORTED_ODOO_VERSION_SPECIFIER)
    if empty_reset:
        return CLI.returner(
            reset_empty_runtime(
                db_name=db_name,
                odoo_bin_path=odoo_main_path / "odoo-bin",
                odoo_conf_path=odoo_conf_path,
                data_dir=data_dir,
                odoo_version=version.major,
                connection=DBConnection.from_odoo_config(db_name, odoo_conf_path),
            )
        )
    template = db_template_name or f"{db_name}_template"
    return CLI.returner(
        reset_runtime_from_template(
            db_name=db_name,
            db_template_name=template,
            odoo_bin_path=odoo_main_path / "odoo-bin",
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
            odoo_version=version.major,
            connection=DBConnection.from_odoo_config(db_name, odoo_conf_path),
        )
    )
