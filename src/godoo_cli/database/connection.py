"""Manage PostgreSQL connections and queries."""

import logging
import os
from collections.abc import Generator
from configparser import ConfigParser
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import psycopg2

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
    sslmode: str | None = None

    @classmethod
    def from_odoo_config(cls, db_name: str, odoo_conf_path: Path | None = None) -> "DBConnection":
        """Load Odoo configuration with PostgreSQL environment overrides."""
        parser = ConfigParser(interpolation=None)
        default_config = Path("~/.odoorc").expanduser()
        legacy_config = Path("~/.openerp_serverrc").expanduser()
        if not default_config.is_file() and legacy_config.is_file():
            default_config = legacy_config
        config_path = odoo_conf_path or Path(os.environ.get("ODOO_RC", str(default_config))).expanduser()
        parser.read(config_path)

        def setting(name: str, envvar: str) -> str:
            value = os.environ.get(envvar, parser.get("options", name, fallback=""))
            return "" if value.lower() in {"false", "none"} else value

        return cls(
            hostname=setting("db_host", "PGHOST"),
            port=int(setting("db_port", "PGPORT") or 0),
            username=setting("db_user", "PGUSER"),
            password=setting("db_password", "PGPASSWORD"),
            db_name=db_name,
            sslmode=setting("db_sslmode", "PGSSLMODE") or None,
        )

    def with_overrides(
        self,
        *,
        hostname: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        sslmode: str | None = None,
    ) -> "DBConnection":
        """Apply explicitly supplied CLI settings over this configured connection."""
        return replace(
            self,
            hostname=self.hostname if hostname is None else hostname,
            port=self.port if port is None else port,
            username=self.username if username is None else username,
            password=self.password if password is None else password,
            sslmode=self.sslmode if sslmode is None else sslmode,
        )

    def with_db(self, db_name: str, *, readonly: bool | None = None) -> "DBConnection":
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
            sslmode=self.sslmode or None,
        )

    @property
    def cli_dict(self) -> dict[str, str | int | None]:
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
