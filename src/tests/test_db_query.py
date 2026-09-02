"""Tests for database bootstrap-status detection."""

from contextlib import contextmanager
from typing import Optional

import pytest
from psycopg2 import OperationalError

from godoo_cli.commands.db.query import DbBootstrapStatus, _is_bootstrapped
from godoo_cli.models import DBConnection


class _DatabaseExistsCursor:
    def execute(self, _statement: str, _params: list[str]) -> None:
        pass

    def fetchone(self) -> tuple[bool]:
        return (True,)


def _connection() -> DBConnection:
    return DBConnection(hostname="db", port=5432, username="odoo", password="secret", db_name="runtime")


def test_with_db_preserves_connection_settings_and_can_change_read_only_mode():
    runtime_connection = _connection()

    maintenance_connection = runtime_connection.with_db("postgres", readonly=True)

    assert maintenance_connection is not runtime_connection
    assert maintenance_connection.hostname == "db"
    assert maintenance_connection.port == 5432
    assert maintenance_connection.username == "odoo"
    assert maintenance_connection.password == "secret"
    assert maintenance_connection.db_name == "postgres"
    assert maintenance_connection.readonly is True
    assert runtime_connection.db_name == "runtime"
    assert runtime_connection.readonly is False


def test_bootstrap_status_reraises_connection_errors_for_existing_database(monkeypatch: pytest.MonkeyPatch):
    message = "connection refused"

    @contextmanager
    def connect(connection: DBConnection):
        if connection.db_name == "runtime":
            raise OperationalError(message)
        yield _DatabaseExistsCursor()

    monkeypatch.setattr(DBConnection, "connect", connect)

    with pytest.raises(OperationalError, match=message):
        _is_bootstrapped(_connection())


def test_bootstrap_status_returns_missing_only_after_maintenance_check(monkeypatch: pytest.MonkeyPatch):
    message = 'database "runtime" does not exist'

    @contextmanager
    def connect(connection: DBConnection):
        if connection.db_name == "runtime":
            raise OperationalError(message)

        class MissingDatabaseCursor:
            def execute(self, _statement: str, _params: list[str]) -> None:
                pass

            def fetchone(self) -> tuple[bool]:
                return (False,)

        yield MissingDatabaseCursor()

    monkeypatch.setattr(DBConnection, "connect", connect)

    assert _is_bootstrapped(_connection()) == DbBootstrapStatus.NO_DB


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
    status_rows: list[Optional[tuple[object, ...]]],
    expected: DbBootstrapStatus,
):
    class RuntimeCursor:
        def __init__(self) -> None:
            self.rows = iter(status_rows)

        def execute(self, _statement: str) -> None:
            pass

        def fetchone(self) -> Optional[tuple[object, ...]]:
            return next(self.rows)

    @contextmanager
    def connect(_connection: DBConnection):
        yield RuntimeCursor()

    monkeypatch.setattr(DBConnection, "connect", connect)

    assert _is_bootstrapped(_connection()) == expected
