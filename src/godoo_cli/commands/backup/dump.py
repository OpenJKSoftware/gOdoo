import logging
import shutil
from configparser import ConfigParser
from datetime import datetime
from pathlib import Path

import typer

from ...cli_common import CommonCLI
from ...helpers.odoo_files import odoo_bin_get_version
from ..db.connection import DBConnection

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


@CLI.arg_annotator
def dump_instance(
    dump_path: Path = typer.Argument(
        ...,
        help="Path to dump to",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    db_name=CLI.database.db_name,
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
    odoo_main_path=CLI.odoo_paths.bin_path,
    conf_path=CLI.odoo_paths.conf_path,
):
    """Dump DB and Filestore into Folder"""
    db_connection = DBConnection(
        db_name=db_name,
        hostname=db_host,
        username=db_user,
        password=db_password,
        port=db_port,
    )
    dump_path.mkdir(parents=True, exist_ok=True)

    if not conf_path:
        raise typer.Exit("No Odoo Conf Path provided. Cannot dump Filestore")

    # Read .conf value [options] datad_dir
    conf_path = Path(conf_path)
    parser = ConfigParser()
    parser.read(conf_path)
    data_dir = parser["options"]["data_dir"]
    data_dir = Path(data_dir)

    # Copy Filestore
    LOGGER.info("Dumping Filestore")
    filestore_target = dump_path / "odoo_filestore"
    shutil.rmtree(filestore_target, ignore_errors=True)
    shutil.copytree(data_dir, filestore_target)

    # Dump DB using pg_dump
    LOGGER.info("Dumping DB")
    db_dump_target = dump_path / "odoo.dump"
    db_dump_target.unlink(missing_ok=True)
    db_connection.run_psql_shell_command("pg_dump --format c {} > %s" % db_dump_target)

    readme_path = Path(dump_path) / "README.md"
    readme_path.unlink(missing_ok=True)

    odoo_version = odoo_bin_get_version(odoo_main_path)

    readme_content = f"""
# gOdoo Dump

SQL Dump and Filestore of gOdoo Instance.

## Metadata


- Odoo Version: {odoo_version.raw}
- Filestore: [{ datetime.fromtimestamp(filestore_target.stat().st_mtime)}]({filestore_target.relative_to(dump_path)})
- SQL Dump: [{ datetime.fromtimestamp(db_dump_target.stat().st_mtime)}]({db_dump_target.relative_to(dump_path)})

"""
    readme_path.write_text(readme_content)
