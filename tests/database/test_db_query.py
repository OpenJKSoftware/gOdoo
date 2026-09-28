"""Tests for database bootstrap-status detection."""

from contextlib import contextmanager

import pytest
from psycopg2 import OperationalError

from godoo_cli.database.connection import DBConnection
from godoo_cli.database.state import DbBootstrapStatus, classify_bootstrap_state


class _DatabaseExistsCursor:
    def execute(self, _statement: str, _params: list[str]) -> None:
        pass

    def fetchone(self) -> tuple[bool]:
        return (True,)


def _connection() -> DBConnection:
    return DBConnection(hostname="db", port=5432, username="odoo", password="secret", db_name="runtime")


def test_bootstrap_status_reraises_connection_errors_for_existing_database(monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that bootstrap status reraises connection errors for existing database."""
    message = "connection refused"

    @contextmanager
    def connect(connection: DBConnection):
        if connection.db_name == "runtime":
            raise OperationalError(message)
        yield _DatabaseExistsCursor()

    monkeypatch.setattr(DBConnection, "connect", connect)

    with pytest.raises(OperationalError, match=message):
        classify_bootstrap_state(_connection())


def test_bootstrap_status_returns_missing_only_after_maintenance_check(monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that bootstrap status returns missing only after maintenance check."""
    message = 'database "runtime" does not exist'
    maintenance_connections: list[DBConnection] = []

    @contextmanager
    def connect(connection: DBConnection):
        if connection.db_name == "postgres":
            maintenance_connections.append(connection)

            class MissingDatabaseCursor:
                def execute(self, _statement: str, _params: list[str] | None = None) -> None:
                    pass

                def fetchone(self) -> tuple[bool]:
                    return (False,)

            yield MissingDatabaseCursor()
            return
        if connection.db_name == "runtime":
            raise OperationalError(message)

    monkeypatch.setattr(DBConnection, "connect", connect)

    assert classify_bootstrap_state(_connection()) == DbBootstrapStatus.NO_DB
    assert maintenance_connections == [DBConnection("db", 5432, "odoo", "secret", "postgres", readonly=True)]
    maintenance_connection = maintenance_connections[0]
    assert maintenance_connection.hostname == "db"
    assert maintenance_connection.port == 5432
    assert maintenance_connection.username == "odoo"
    assert maintenance_connection.password == "secret"
    assert maintenance_connection.db_name == "postgres"
    assert maintenance_connection.readonly is True
    assert _connection().db_name == "runtime"
    assert _connection().readonly is False


@pytest.mark.parametrize(
    ("status_rows", "expected"),
    [
        ([(False, False)], DbBootstrapStatus.EMPTY_DB),
        ([(True, False)], DbBootstrapStatus.INVALID_DB),
        ([(True, True), None], DbBootstrapStatus.INVALID_DB),
        ([(True, True), ("to install",)], DbBootstrapStatus.INVALID_DB),
        ([(True, True), ("installed",)], DbBootstrapStatus.BOOTSTRAPPED),
        ([(True, True), ("to upgrade",)], DbBootstrapStatus.BOOTSTRAPPED),
    ],
)
def test_bootstrap_status_requires_a_usable_odoo_base_module(
    monkeypatch: pytest.MonkeyPatch,
    status_rows: list[tuple[object, ...] | None],
    expected: DbBootstrapStatus,
):
    """Guards the contract that bootstrap status requires a usable odoo base module."""

    class RuntimeCursor:
        def __init__(self) -> None:
            self.rows = iter(status_rows)

        def execute(self, _statement: str) -> None:
            pass

        def fetchone(self) -> tuple[object, ...] | None:
            return next(self.rows)

    @contextmanager
    def connect(_connection: DBConnection):
        yield RuntimeCursor()

    monkeypatch.setattr(DBConnection, "connect", connect)

    assert classify_bootstrap_state(_connection()) == expected
