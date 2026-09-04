"""Manage PostgreSQL connections and queries."""

import logging
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, Optional, Union

import psycopg2

from ..helpers.system import run_cmd

LOGGER = logging.getLogger(__name__)


@dataclass
class DBConnection:
    """Store connection settings and run PostgreSQL operations."""

    hostname: str
    port: int
    username: str
    password: str
    db_name: str
    conn_timeout = 10
    readonly: bool = False

    def with_db(self, db_name: str, *, readonly: Optional[bool] = None) -> "DBConnection":
        """Return equivalent connection settings for another database.

        This returns connection *settings*, not a live PostgreSQL connection.
        It keeps the configured server and credentials when a maintenance
        database must be contacted instead of the runtime database.
        """
        return replace(self, db_name=db_name, readonly=self.readonly if readonly is None else readonly)

    def get_connection(self):
        """Open a PostgreSQL connection."""
        LOGGER.debug(
            "Connecting to DB: '%s:%s' U='%s' D='%s'",
            self.hostname,
            self.port,
            self.username,
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
    def cli_dict(self) -> dict[str, Optional[Union[str, int]]]:
        """Return CLI-compatible connection options."""
        return {
            "db_host": self.hostname,
            "db_port": self.port,
            "db_name": self.db_name,
            "db_user": self.username,
            "db_password": self.password,
        }

    @contextmanager
    def connect(self) -> Generator[psycopg2.extensions.cursor, None, None]:
        """Yield a cursor, committing writable work or rolling it back on failure."""
        connection = self.get_connection()
        cr = connection.cursor()
        try:
            yield cr
            if not self.readonly:
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

    def run_psql_shell_command(self, command: str, **kwargs: Any) -> subprocess.CompletedProcess:
        """Run a psql command using the provided credentials.

        {} in the command will get templated with the connection string.

        Returns:
            The completed psql process.
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
