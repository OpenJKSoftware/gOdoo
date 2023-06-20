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

from ...cli_common import CommonCLI

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)


@CLI.unpacker
@CLI.arg_annotator
def import_to_odoo(
    read_paths: List[Path] = typer.Argument(
        ...,
        readable=True,
        exists=True,
        help="Folder in which to search for import",
    ),
    rpc_host=CLI.rpc.rpc_host,
    rpc_database=CLI.rpc.rpc_db_name,
    rpc_user=CLI.rpc.rpc_user,
    rpc_password=CLI.rpc.rpc_password,
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
    Import [bold green]csv, xlsx, json, .py [/bold green] files into Odoo.
    Adds an ir.config_parameter containing timestamp of each imported file.
    """
    odoo_api = wait_for_odoo(
        odoo_host=rpc_host,
        odoo_db=rpc_database,
        odoo_user=rpc_user,
        odoo_password=rpc_password,
    )

    if missing_paths := [p for p in read_paths if not p.exists()]:
        raise ValueError("Provided import Paths: %s doesn't exist", ", ".join(missing_paths))

    for path in read_paths:
        import_data(
            odoo_api=odoo_api,
            read_path=path.absolute(),
            data_regex=file_regex,
            product_image_regex=False if path.is_file() else product_image_regex,
            check_dataset_timestamp=check_data_timestamp,
            skip_existing_ids=skip_existing_ids,
        )
