import logging
import subprocess
from pathlib import Path

import typer
from rich.progress import track
from typing_extensions import Annotated

from ...cli_common import CommonCLI
from ...helpers.system import typer_ask_overwrite_path

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


class InstancePuller:
    ssh_hostname: str
    ssh_user: str
    pg_container: str  # Does Postgres Live in a container?
    pg_user: str

    def set_exec_target(self, command):
        """Prepend ssh execution, if ssh args are provided"""
        if self.ssh_hostname:
            return f"ssh {self.ssh_user}@{self.ssh_hostname} '{command}'"
        return command

    def get_docker_volume_path(self, volume_name: str):
        command = f"docker volume inspect {volume_name} | jq -r .[0].Mountpoint"
        command = self.set_exec_target(command)
        LOGGER.debug("Running: '%s'", command)
        path = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
        ).stdout.strip("\n")
        LOGGER.debug("Docker Volume Path: %s", path)
        return path

    def rsync_filestore(self, filestore_folder, target_folder: Path):
        src_path = filestore_folder
        if self.ssh_hostname:
            src_path = f"{self.ssh_user}@{self.ssh_hostname}:{src_path}/"

        command = f"rsync --rsync-path 'sudo rsync' -a --no-perms --no-owner --no-group --delete --info=progress2 {src_path} {target_folder}"
        LOGGER.info("Rysnc filestore from: %s", filestore_folder)
        LOGGER.debug("Running: %s", command)
        return subprocess.run(command, shell=True).returncode

    def download_db_dump(self, db_name, target_sql_path: Path):
        command = f"pg_dump --no-owner -Fc {db_name}"
        if n := self.pg_container:
            command = f"docker exec {n} {command}"
            if u := self.pg_user:
                command += f" -U {u}"
        else:
            # Lets assume we run pg_dump locally here.
            # Totally need to think about a neat way to handle the multiple combinations of this.
            if u := self.pg_user:
                command = f"sudo -u {u} {command}"

        command = self.set_exec_target(command)
        LOGGER.info("Downloading DB Dump: %s", db_name)
        target_sql_path.touch(exist_ok=True)
        target_sql_path.unlink()
        ret = 0
        with target_sql_path.open("wb") as sql_dump_file:
            LOGGER.debug("Running: %s", command)
            dbdumper = subprocess.Popen(command, stdout=subprocess.PIPE, shell=True)
            for line in track(dbdumper.stdout, description="Downloading DB Dump", show_speed=False):
                sql_dump_file.write(line)
            ret = dbdumper.wait()
        if ret != 0:
            LOGGER.error("Failed to download DB Dump")
            raise typer.Exit(1)

    @CLI.arg_annotator
    def pull_instance_data(
        self,
        target_folder: Annotated[
            Path, typer.Argument(..., help="Target Folder to pull data to.", file_okay=False, dir_okay=True)
        ],
        ssh_user: Annotated[
            str,
            typer.Option(
                ..., help="SSH User of remote instance.", rich_help_panel="Remote Options", envvar="ODOO_PULL_SSH_USER"
            ),
        ] = None,
        ssh_hostname: Annotated[
            str,
            typer.Option(
                help="SSH Hostname of remote instance.", rich_help_panel="Remote Options", envvar="ODOO_PULL_HOST"
            ),
        ] = None,
        filestore_folder: Annotated[
            Path,
            typer.Option(help="Path to odoo web folder.", rich_help_panel="Filestore Options"),
        ] = None,
        filestore_volume: Annotated[
            str,
            typer.Option(
                help="Alternative to valib-folder. A Docker volume name, that gets used to derive filestore-folder",
                rich_help_panel="Filestore Options",
            ),
        ] = None,
        pg_container: Annotated[
            str,
            typer.Option(
                help="Postgres Container Name. (Optional)",
                rich_help_panel="Database Options",
            ),
        ] = None,
        pg_db_user=CLI.database.db_user,
        pg_db_name=CLI.database.db_name,
    ):
        """Pull filestore folder and Database Dump and save to TARGET_FOLDER"""
        self.ssh_hostname = ssh_hostname
        self.ssh_user = ssh_user
        filestore_target = target_folder / "odoo_filestore"
        sql_target = target_folder / "odoo.dump"

        if not typer_ask_overwrite_path([filestore_target, sql_target]):
            raise typer.Exit(2)

        if filestore_folder:
            volume_path = filestore_folder
        elif filestore_volume:
            volume_path = self.get_docker_volume_path(filestore_volume)
        else:
            LOGGER.error("You need to either Supply a filestore-folder or a filestore-volume")
            raise typer.Exit(1)

        filestore_target.mkdir(parents=True, exist_ok=True)
        self.rsync_filestore(volume_path, filestore_target)

        self.pg_container = pg_container
        self.pg_user = pg_db_user
        self.download_db_dump(pg_db_name, sql_target)
        LOGGER.info("Done Pulling Odoo Instance")
