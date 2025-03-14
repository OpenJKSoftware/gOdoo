"""Database Connection and Management.

This module provides functionality for managing database connections and
executing database queries using the psycopg2 library.
"""

import logging
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Optional

import psycopg2

from ...cli_common import CommonCLI
from ...helpers.system import run_cmd

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def login_db(
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
):
    """Launch an interactive psql CLI session with the provided credentials.

    This function starts an interactive PostgreSQL command-line session using
    the provided database connection parameters.

    Args:
        db_host: Database host address.
        db_port: Database port number.
        db_name: Name of the database to connect to.
        db_user: Database username.
        db_password: Database password.
    """
    command = ["psql", f"-h{db_host}", f"-U{db_user}", f"-d{db_name}"]
    if db_port != 0:
        command.append(f"-p{db_port}")
    subprocess.run(command, env={"PGPASSWORD": db_password})


@dataclass
class DBConnection:
    """Database connection configuration and management class.

    This class handles database connection details and provides methods
    for executing database commands and managing connections.

    Attributes:
        hostname: Database server hostname.
        port: Database server port.
        username: Database username.
        password: Database password.
        db_name: Name of the database.
    """

    hostname: str
    port: int
    username: str
    password: str
    db_name: str
    conn_timeout = 10

    def get_connection(self):
        """Get a database connection."""
        LOGGER.debug(
            "Connecting to DB: '%s:%s' U='%s' P='%s' D='%s'",
            self.hostname,
            self.port,
            self.username,
            self.password,
            self.db_name,
        )
        return psycopg2.connect(
            host=self.hostname,
            port=self.port or None,
            user=self.username,
            password=self.password,
            dbname=self.db_name,
            connect_timeout=self.conn_timeout,
        )

    @property
    def cli_dict(self) -> dict[str, Optional[str]]:
        """Get connection parameters as a dictionary.

        Returns:
            Dict[str, Optional[str]]: Dictionary of connection parameters.
        """
        return {
            "db_host": self.hostname,
            "db_port": self.port,
            "db_name": self.db_name,
            "db_user": self.username,
            "db_password": self.password,
        }

    @contextmanager
    def connect(self) -> Generator[psycopg2.extensions.cursor, None, None]:
        """Create a database connection and cursor.

        This context manager creates a database connection and cursor,
        handling transaction management and resource cleanup.

        Yields:
            psycopg2.cursor: A database cursor for executing queries.

        Raises:
            Exception: Any database-related exception that occurs during
                connection or query execution.
        """
        connection = self.get_connection()
        cr = connection.cursor()
        try:
            yield cr
            LOGGER.debug("Committing DB cursor")
            connection.commit()
        except Exception as e:
            LOGGER.warning("Rolling Back DB cursor. Got Exception: %s", e)
            connection.rollback()
            raise e
        finally:
            LOGGER.debug("Closing DB connection")
            cr.close()
            connection.close()

    def run_psql_shell_command(self, command: str, **kwargs: dict[str, Any]) -> subprocess.CompletedProcess:
        """Run a psql command using the provided credentials.

        {} in the command will get templated with the connection string.
        """
        LOGGER.debug("Running PSQL Command: %s", command)
        arg_list = []
        if h := self.hostname:
            arg_list += ["-h", h]
        if p := self.port:
            arg_list += ["-p", p]
        if u := self.username:
            arg_list += ["-U", u]
        if d := self.db_name:
            arg_list += ["-d", d]
        command_env = {}
        if p := self.password:
            command_env["PGPASSWORD"] = p

        arg_str = " ".join([str(arg) for arg in arg_list])
        if "{}" in command:
            command = command.format(arg_str)
        else:
            command += " " + " ".join(arg_list)

        return run_cmd(command, env=command_env, **kwargs)
