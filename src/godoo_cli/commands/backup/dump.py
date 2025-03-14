"""Database dump functionality for Odoo instances.

This module provides functionality for creating database dumps from Odoo instances,
including support for various dump formats and compression options.
"""

import logging
from configparser import ConfigParser
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from ...cli_common import CommonCLI
from ...helpers.odoo_files import odoo_bin_get_version
from ..db.connection import DBConnection
from .util import call_rsync

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def dump_instance(
    dump_path: Annotated[
        Path,
        typer.Argument(
            help="Path to dump to",
            file_okay=False,
            dir_okay=True,
            writable=True,
            resolve_path=True,
            envvar="GODOO_DUMP_PATH",
        ),
    ],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_name: Annotated[str, CLI.database.db_name],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_user: Annotated[str, CLI.database.db_user] = "",
    db_password: Annotated[str, CLI.database.db_password] = "",
):
    """Dump DB and Filestore into Folder."""
    db_connection = DBConnection(
        db_name=db_name,
        hostname=db_host,
        username=db_user,
        password=db_password,
        port=db_port,
    )
    if not conf_path.exists():
        msg = f"No Odoo Conf Path provided or doesnt exists: '{conf_path}'"
        LOGGER.error(msg)
        raise typer.Exit(msg)

    if not dump_path.exists():
        msg = f"No Dump Path provided or doesnt exists: '{dump_path}'"
        LOGGER.error(msg)
        raise typer.Exit(msg)

    # Read .conf value [options] datad_dir
    parser = ConfigParser()
    parser.read(conf_path)
    data_dir = parser["options"]["data_dir"]
    data_dir = Path(data_dir)

    # Copy Filestore
    filestore_target = dump_path / "odoo_filestore"
    LOGGER.info("Dumping Filestore -> %s", filestore_target)
    call_rsync(source_folder=data_dir, target_folder=filestore_target)

    # Dump DB using pg_dump
    db_dump_target = dump_path / "odoo.dump"
    LOGGER.info("Dumping DB -> %s", db_dump_target)
    db_dump_target.unlink(missing_ok=True)
    db_connection.run_psql_shell_command(f"pg_dump --format c {{}} > {db_dump_target}")
    LOGGER.info("SQL Dump Completed with size -> %.2f MB", db_dump_target.stat().st_size / (1024 * 1024))

    readme_path = dump_path / "README.md"
    readme_path.unlink(missing_ok=True)
    odoo_version = odoo_bin_get_version(odoo_main_path)

    readme_content = f"""
# gOdoo Dump

SQL Dump and Filestore of gOdoo Instance.

## Metadata


- Odoo Version: {odoo_version.raw}
- Filestore: [{datetime.fromtimestamp(filestore_target.stat().st_mtime)}]({filestore_target.relative_to(dump_path)})
- SQL Dump: [{datetime.fromtimestamp(db_dump_target.stat().st_mtime)}]({db_dump_target.relative_to(dump_path)})

"""
    readme_path.write_text(readme_content)
    LOGGER.info("Odoo Dump Completed -> %s", dump_path)
