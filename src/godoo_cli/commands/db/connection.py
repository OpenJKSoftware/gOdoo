import logging
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg2

from ...cli_common import CommonCLI
from ...helpers.system import run_cmd

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


@CLI.arg_annotator
def login_db(
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
):
    """Login Interactive psql CLI using provided credentials"""
    command = ["psql", f"-h{db_host}", f"-p{db_port}", f"-U{db_user}", f"-d{db_name}"]
    subprocess.run(command, env={"PGPASSWORD": db_password})


@dataclass
class DBConnection:
    db_name: str
    hostname: str
    username: str
    password: str
    port: int = 0
    conn_timeout: int = 5

    def get_connection(self):
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
    def cli_dict(self):
        """Return a dict that when expanded Matches the Default DB connection Args used throuout gOdoo"""
        return {
            "db_host": self.hostname,
            "db_port": self.port,
            "db_name": self.db_name,
            "db_user": self.username,
            "db_password": self.password,
        }

    @contextmanager
    def connect(self):
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

    def run_psql_shell_command(self, command: str, **kwargs):
        """Run a psql command using the provided credentials. {} in the command will get templated with the connection string"""
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

        arg_str = " ".join(arg_list)
        if "{}" in command:
            command = command.format(arg_str)
        else:
            command += " " + " ".join(arg_list)

        return run_cmd(command, env=command_env, **kwargs)
