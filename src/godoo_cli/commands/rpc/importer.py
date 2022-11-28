"""
Import read_path into Odoo.
Will wait until Odoo is Online.
Places Systemparam to Track if import was already done.
"""

import logging
from pathlib import Path
from typing import List

import typer
from godoo_rpc import import_data
from godoo_rpc.login import wait_for_odoo

from ...helpers.cli import typer_unpacker
from .cli import rpc_callback

app = typer.Typer(callback=rpc_callback, no_args_is_help=True)
LOGGER = logging.getLogger(__name__)


@typer_unpacker
def import_to_odoo(
    ctx: typer.Context,
    read_paths: List[Path] = typer.Argument(
        ...,
        readable=True,
        exists=True,
        help="Folder in which to search for import",
    ),
    file_regex: str = typer.Option(
        r"(?P<module>.*)\.(csv|py|xlsx|json)$",
        help="Regex for filesearch. Add group 'module' to set a Module for RPC import",
    ),
    product_image_regex: str = typer.Option(
        r"(?P<default_code>\d{6})\.(jpeg|png|jpg)$",
        help="Regex to search for Product images. Add Fields as regex group for Matching.",
    ),
    check_data_timestamp: bool = typer.Option(
        True,
        help="If true, Odoo remembers the Name of an uploaded File in a Serverparameter. Subsequent Imports will ignore the file if it hasnt changed.",
    ),
    skip_existing_ids: bool = typer.Option(False, help="Will skip import of already existing External IDs."),
):
    """
    Import Csv Files into Odoo.
    Adds an ir.config_parameter for each imported file,
    containing the modification time of the file.
    Will Skip already matching Timestamps and only import files that are new.
    """
    odoo_api = wait_for_odoo(
        odoo_host=ctx.obj.odoo_rpc_host,
        odoo_db=ctx.obj.odoo_main_db,
        odoo_user=ctx.obj.odoo_rpc_user,
        odoo_password=ctx.obj.odoo_rpc_password,
    )

    if missing_paths := [p for p in read_paths if not p.exists()]:
        raise ValueError("Provided import Paths: %s doesn't exist", ", ".join(missing_paths))

    for path in read_paths:
        import_data(
            odoo_api=odoo_api,
            read_path=path.absolute(),
            data_regex=file_regex,
            product_image_regex="" if path.is_file() else product_image_regex,
            check_dataset_timestamp="" if path.is_file() else check_data_timestamp,
            skip_existing_ids=skip_existing_ids,
        )
