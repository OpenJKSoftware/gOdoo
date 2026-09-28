"""Central PostgreSQL subprocess construction and credential handling."""

import logging
import os
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from .connection import DBConnection

LOGGER = logging.getLogger(__name__)


def postgres_argv(connection: DBConnection, *, database: str | None = None) -> list[str]:
    """Return PostgreSQL client connection arguments without credentials in argv."""
    return [
        *(["--host", connection.hostname] if connection.hostname else []),
        *(["--port", str(connection.port)] if connection.port else []),
        *(["--username", connection.username] if connection.username else []),
        *(
            ["--dbname", selected_database]
            if (selected_database := database if database is not None else connection.db_name)
            else []
        ),
    ]


def redact_postgres_argv(argv: Sequence[str]) -> list[str]:
    """Return a safe command representation for operational logs."""
    return list(argv)


@contextmanager
def postgres_environment(connection: DBConnection, *, on_error_stop: bool = False) -> Iterator[dict[str, str]]:
    """Yield PostgreSQL client environment with secrets and SSL outside argv."""
    environment = dict(os.environ)
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGPASSWORD", "PGSSLMODE"):
        environment.pop(name, None)
    if connection.password:
        environment["PGPASSWORD"] = connection.password
    if connection.sslmode:
        environment["PGSSLMODE"] = connection.sslmode
    if not on_error_stop:
        yield environment
        return
    with tempfile.TemporaryDirectory(prefix="godoo-psql-") as directory:
        psqlrc = Path(directory) / "psqlrc"
        psqlrc.write_text("\\set ON_ERROR_STOP on\n", encoding="utf-8")
        environment["PSQLRC"] = str(psqlrc)
        yield environment
