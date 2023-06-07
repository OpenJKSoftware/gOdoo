import logging
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg2

from ...cli_common import CommonCLI

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
        finally:
            LOGGER.debug("Closing DB connection")
            cr.close()
            connection.close()
