"""Database settings used by gOdoo operations."""

from dataclasses import dataclass
from functools import cached_property

from .connection import DBConnection


@dataclass(frozen=True)
class DatabaseSettings:
    """Immutable database identity used by Odoo and gOdoo operations."""

    db_user: str = ""
    db_password: str = ""
    db_host: str = ""
    db_port: int = 0
    db_name: str = ""
    db_filter: str = ""
    db_sslmode: str | None = None

    @cached_property
    def db_connection(self) -> DBConnection:
        """Return the DBConnection adapter for these settings."""
        return DBConnection(
            hostname=self.db_host,
            port=self.db_port,
            username=self.db_user,
            password=self.db_password,
            db_name=self.db_name,
            sslmode=self.db_sslmode,
        )
