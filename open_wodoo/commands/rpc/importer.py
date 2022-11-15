"""
Import read_path into Odoo.
Will wait until Odoo is Online.
Places Systemparam to Track if import was already done.
"""

import logging
import re
from pathlib import Path

import typer
from wodoo_rpc import import_folder
from wodoo_rpc.login import wait_for_odoo

from .cli import rpc_callback

app = typer.Typer(callback=rpc_callback)
LOGGER = logging.getLogger(__name__)


def wodoo_import_folder(
    ctx: typer.Context,
    read_path: Path = typer.Option(
        ...,
        dir_okay=True,
        file_okay=False,
        readable=True,
        exists=True,
        help="Folder in which to search for import",
    ),
    file_regex: str = typer.Option(
        r"(?P<module>.*)\.(csv|py|xlsx|json)$",
        help="Regex for filesearch. Add group 'module' to set a Module for RPC import",
    ),
    image_regex: str = typer.Option(
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

    import_folder(
        odoo_api=odoo_api,
        read_path=read_path.absolute(),
        data_regex=re.compile(file_regex),
        image_regex=re.compile(image_regex),
        check_dataset_timestamp=check_data_timestamp,
        skip_existing_ids=skip_existing_ids,
    )
