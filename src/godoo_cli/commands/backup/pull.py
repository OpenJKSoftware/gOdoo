"""Remote backup retrieval functionality for Odoo instances.

This module provides functionality for pulling database backups from remote sources,
supporting various transfer methods and backup formats.
"""

import logging
import subprocess
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.progress import track

from ...cli_common import CommonCLI
from ...helpers.system import typer_ask_overwrite_path

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)


class InstancePuller:
    """Class for pulling complete Odoo instance data.

    This class handles pulling both database dumps and filestore data from
    remote Odoo instances, supporting various deployment configurations including
    Docker containers and SSH connections.

    Attributes:
        ssh_hostname: Remote host for SSH connection.
        ssh_user: Username for SSH connection.
        pg_container: Name of the PostgreSQL container.
        pg_user: PostgreSQL user for authentication.
    """

    ssh_hostname: str
    ssh_user: str
    pg_container: str  # Does Postgres Live in a container?
    pg_user: str

    def set_exec_target(self, command: str) -> str:
        """Prepare a command for remote execution if needed.

        This method prepends SSH execution details to the command if SSH
        arguments are provided.

        Args:
            command: The command to be executed.

        Returns:
            str: The command modified for remote execution if needed.
        """
        if self.ssh_hostname:
            return f"ssh {self.ssh_user}@{self.ssh_hostname} '{command}'"
        return command

    def get_docker_volume_path(self, volume_name: str) -> str:
        """Get the mount path of a Docker volume.

        This method retrieves the actual filesystem path where a Docker volume
        is mounted.

        Args:
            volume_name: Name of the Docker volume.

        Returns:
            str: The filesystem path where the volume is mounted.
        """
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

    def rsync_filestore(self, filestore_folder: str, target_folder: Path) -> int:
        """Synchronize the Odoo filestore using rsync.

        This method copies the filestore data from a remote or local source
        to the target location.

        Args:
            filestore_folder: Source path of the filestore.
            target_folder: Destination path for the filestore.

        Returns:
            int: Return code from rsync (0 for success).
        """
        src_path = filestore_folder
        if self.ssh_hostname:
            src_path = f"{self.ssh_user}@{self.ssh_hostname}:{src_path}/"

        command = f"rsync --rsync-path 'sudo rsync' -a --no-perms --no-owner --no-group --delete --info=progress2 {src_path} {target_folder}"
        LOGGER.info("Rysnc filestore from: %s", filestore_folder)
        LOGGER.debug("Running: %s", command)
        return subprocess.run(command, shell=True).returncode

    def download_db_dump(self, db_name: str, target_sql_path: Path) -> None:
        """Download a database dump from PostgreSQL.

        This method creates a database dump using pg_dump, supporting both
        local and containerized PostgreSQL instances.

        Args:
            db_name: Name of the database to dump.
            target_sql_path: Path where to save the dump file.

        Raises:
            typer.Exit: If the database dump fails.
        """
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

    def pull_instance_data(
        self,
        target_folder: Annotated[
            Path, typer.Argument(help="Target Folder to pull data to.", file_okay=False, dir_okay=True)
        ],
        pg_db_user: Annotated[str, CLI.database.db_user],
        pg_db_name: Annotated[str, CLI.database.db_name],
        ssh_user: Annotated[
            Optional[str],
            typer.Option(
                help="SSH User of remote instance.", rich_help_panel="Remote Options", envvar="ODOO_PULL_SSH_USER"
            ),
        ] = None,
        ssh_hostname: Annotated[
            Optional[str],
            typer.Option(
                help="SSH Hostname of remote instance.", rich_help_panel="Remote Options", envvar="ODOO_PULL_HOST"
            ),
        ] = None,
        filestore_folder: Annotated[
            Optional[Path],
            typer.Option(help="Path to odoo web folder.", rich_help_panel="Filestore Options"),
        ] = None,
        filestore_volume: Annotated[
            Optional[str],
            typer.Option(
                help="Alternative to valib-folder. A Docker volume name, that gets used to derive filestore-folder.",
                rich_help_panel="Filestore Options",
            ),
        ] = None,
        pg_container: Annotated[
            Optional[str],
            typer.Option(
                help="Postgres Container Name. (Optional)",
                rich_help_panel="Database Options",
            ),
        ] = None,
    ):
        """Pull filestore folder and database dump from a remote instance.

        This method retrieves both the filestore and a database dump from a remote
        Odoo instance, supporting various deployment configurations including
        Docker containers and SSH connections.

        Args:
            target_folder: Local directory to store the pulled data.
            ssh_user: Username for SSH connection.
            ssh_hostname: Remote host for SSH connection.
            filestore_folder: Direct path to the Odoo filestore.
            filestore_volume: Docker volume name containing the filestore.
            pg_container: Name of the PostgreSQL container.
            pg_db_user: PostgreSQL user for authentication.
            pg_db_name: Name of the database to dump.

        Raises:
            typer.Exit: If the pull operation fails.
        """
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
