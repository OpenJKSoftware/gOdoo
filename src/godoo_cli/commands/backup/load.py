import logging
import subprocess
from pathlib import Path

import typer
from typing_extensions import Annotated

from ...cli_common import CommonCLI

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def rsync(source_folder, target_folder: Path):
    """Copy Folder to anohter using Rsync"""
    src_path = source_folder / "odoo_filestore"
    if not src_path.exists() and not src_path.glob("*"):
        raise FileNotFoundError("Cannot find filestore Backup @ %s" % src_path)
    command = f"rsync -a --no-perms --no-owner --no-group --delete --info=progress2 {src_path} {target_folder}"
    LOGGER.info("Rysnc filestore to: %s", source_folder)
    LOGGER.debug("Running: %s", command)
    return subprocess.run(command, shell=True).returncode


def run_command(command: str, log_name: str = "Running Command", **kwargs):
    """Run command usind subprocess.run. All kwargs get passed to subprocess.run. Logger.error if returncode != 0"""
    LOGGER.debug("Running: %s", command)
    ret = subprocess.run(command, **kwargs)
    if ret.returncode != 0:
        LOGGER.error("Failed %s: %s", log_name, ret)
    return ret


def load_pg_dump(
    dump_path: Path,
    db_name: str,
    db_host: str = None,
    db_port: int = 0,
    db_user: str = None,
    db_password: str = None,
):
    """Drop and recreate db_name and restore dump_path into it."""
    if not dump_path.exists():
        raise FileNotFoundError("Cannot find PG dump @ %s" % dump_path)

    psql_connection = ""
    if h := db_host:
        psql_connection += f" -h {h}"
    if p := db_port:
        psql_connection += f" -p {p}"
    if u := db_user:
        psql_connection += f" -U {u}"
    command_env = {}
    if p := db_password:
        command_env["PGPASSWORD"] = p
    if dump_path.suffix == ".dump":
        command = f"pg_restore --no-owner --format c --dbname {db_name} --jobs 8 {psql_connection} {dump_path}"
    else:
        command = f"cat {dump_path} | psql {psql_connection} {db_name} >/dev/null"

    LOGGER.info("Dropping Odoo DB")
    run_command(
        command=f"dropdb {psql_connection} {db_name} --if-exists", log_name="Dropping DB", shell=True, env=command_env
    )
    LOGGER.info("Creating Odoo DB")
    run_command(command=f"createdb {psql_connection} {db_name}", log_name="Creating DB", shell=True, env=command_env)
    LOGGER.info("Loading DB Dump: %s --> %s", dump_path, db_name)
    load_return = run_command(
        command=command, log_name="Reading DB", shell=True, env=command_env, text=True, capture_output=True
    )
    LOGGER.info("Deleting RPC Import Cache using plain SQL")
    rpc_import_delete_cmd = f"echo \"DELETE FROM ir_config_parameter WHERE key='godoo_rpc_import_cache';\" | psql {psql_connection} -d {db_name}"
    run_command(
        rpc_import_delete_cmd,
        log_name="Deleting RPC Import Cache",
        shell=True,
        env=command_env,
        capture_output=True,
        text=True,
    )

    return load_return.returncode


@CLI.arg_annotator
def load_instance_data(
    source_folder: Annotated[Path, typer.Argument(..., help="Source Folder to load data from.")],
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
    dump_file_name: Annotated[
        str, typer.Option(help="Optional Changeable filename of Odoo SQL Dump within source_folder")
    ] = "odoo.dump",
):
    """Copy Filestore cache and load SQL Dump into Postgres for gOdoo."""
    load_pg_dump(
        dump_path=source_folder / dump_file_name,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
    )
    LOGGER.info("Done Loading Odoo instance from %s", source_folder)
