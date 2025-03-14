"""Data import functionality for Odoo via RPC.

This module provides tools for importing data into a running Odoo instance
using Remote Procedure Call (RPC) methods. It supports importing data from
various file formats and sources.
"""

import logging
from pathlib import Path
from typing import Annotated

import typer
from godoo_rpc import import_data
from godoo_rpc.login import wait_for_odoo

from ...cli_common import CommonCLI

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)


def import_to_odoo(
    read_paths: Annotated[
        list[Path],
        typer.Argument(readable=True, exists=True, help="Folder in which to search for import"),
    ],
    rpc_host: Annotated[str, CLI.rpc.rpc_host],
    rpc_database: Annotated[str, CLI.rpc.rpc_db_name],
    rpc_user: Annotated[str, CLI.rpc.rpc_user],
    rpc_password: Annotated[str, CLI.rpc.rpc_password],
    file_regex: Annotated[
        str,
        typer.Option(
            help="Regex for filesearch. Add group 'module' to set a Module for RPC import",
        ),
    ] = r"(?P<module>.*)\.(csv|py|xlsx|json)$",
    product_image_regex: Annotated[
        str,
        typer.Option(
            help="Regex to search for Product images. Add Fields as regex group for Matching.",
        ),
    ] = r"(?P<default_code>\d{6})\.(jpeg|png|jpg)$",
    check_data_timestamp: Annotated[
        bool,
        typer.Option(
            help="If true, Odoo remembers the Name of an uploaded File in a Serverparameter. Subsequent Imports will ignore the file if it hasnt changed.",
        ),
    ] = True,
    skip_existing_ids: Annotated[
        bool,
        typer.Option(
            help="Will skip import of already existing External IDs.",
        ),
    ] = False,
):
    """Import data into a running Odoo instance.

    This function allows importing data from various file paths into an Odoo
    database using RPC. It supports specifying a target model and additional
    import context.
    """
    odoo_api = wait_for_odoo(
        odoo_host=rpc_host,
        odoo_db=rpc_database,
        odoo_user=rpc_user,
        odoo_password=rpc_password,
    )

    if missing_paths := [p for p in read_paths if not p.exists()]:
        msg = f"Provided import Paths: {', '.join(missing_paths)} doesn't exist"
        LOGGER.error(msg)
        raise ValueError(msg)

    for path in read_paths:
        import_data(
            odoo_api=odoo_api,
            read_path=path.absolute(),
            data_regex=file_regex,
            product_image_regex=False if path.is_file() else product_image_regex,
            check_dataset_timestamp=check_data_timestamp,
            skip_existing_ids=skip_existing_ids,
        )
