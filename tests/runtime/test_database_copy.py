"""Tests PostgreSQL database-copy strategy selection."""

from unittest.mock import MagicMock

import psycopg2.errors
import pytest

from godoo_cli.runtime.promotion import create_database


def _database_connection() -> tuple[MagicMock, MagicMock, MagicMock]:
    connection = MagicMock()
    connection.db_name = "staged database"
    admin = MagicMock()
    cursor = admin.cursor.return_value.__enter__.return_value
    connection.with_db.return_value.get_connection.return_value = admin
    return connection, admin, cursor


def _executed_sql(cursor: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in cursor.execute.call_args_list]


def test_database_copy_keeps_default_strategy_without_opt_in() -> None:
    """Leaves the existing database-copy path unchanged by default."""
    connection, _admin, cursor = _database_connection()

    create_database(connection, "template")

    statements = _executed_sql(cursor)
    assert "SHOW server_version_num" not in statements
    assert "STRATEGY FILE_COPY" not in statements[-1]


@pytest.mark.parametrize(
    ("server_version", "uses_file_copy"),
    [(140000, False), (150000, True), (170000, True)],
)
def test_prefer_cow_selects_file_copy_from_postgres_15(server_version: int, uses_file_copy: bool) -> None:
    """Uses FILE_COPY only on PostgreSQL versions that support it."""
    connection, admin, cursor = _database_connection()
    cursor.fetchone.return_value = (server_version,)

    create_database(connection, "template database", prefer_cow=True)

    statements = _executed_sql(cursor)
    create_statement = statements[-1]
    assert ("STRATEGY FILE_COPY" in create_statement) is uses_file_copy
    assert "staged database" in create_statement
    assert "template database" in create_statement
    admin.close.assert_called_once_with()


def test_prefer_cow_uses_clone_when_postgres_advertises_it() -> None:
    """Selects PostgreSQL's clone copy method when it is available."""
    connection, _admin, cursor = _database_connection()
    cursor.fetchone.side_effect = [(180000,), (True,)]

    create_database(connection, "template", prefer_cow=True)

    statements = _executed_sql(cursor)
    assert "SELECT 'clone' = ANY(enumvals) FROM pg_settings WHERE name = 'file_copy_method'" in statements
    assert "SET file_copy_method TO 'clone'" in statements
    assert "STRATEGY FILE_COPY" in statements[-1]


def test_prefer_cow_uses_regular_file_copy_when_clone_is_unavailable() -> None:
    """Keeps PostgreSQL's normal file-copy method when clone is unavailable."""
    connection, _admin, cursor = _database_connection()
    cursor.fetchone.side_effect = [(180000,), (False,)]

    create_database(connection, "template", prefer_cow=True)

    statements = _executed_sql(cursor)
    assert "SET file_copy_method TO 'clone'" not in statements
    assert "STRATEGY FILE_COPY" in statements[-1]


def test_prefer_cow_retries_regular_file_copy_when_clone_is_unsupported() -> None:
    """Falls back once when the filesystem cannot clone PostgreSQL files."""
    connection, _admin, cursor = _database_connection()
    cursor.fetchone.side_effect = [(180000,), (True,)]
    create_attempts = 0

    def execute(statement: object, *_args: object) -> None:
        nonlocal create_attempts
        if "CREATE DATABASE" in str(statement) and create_attempts == 0:
            create_attempts += 1
            message = "clone is unavailable"
            raise psycopg2.errors.FeatureNotSupported(message)

    cursor.execute.side_effect = execute

    create_database(connection, "template", prefer_cow=True)

    statements = _executed_sql(cursor)
    assert sum("CREATE DATABASE" in statement for statement in statements) == 2
    assert "SET file_copy_method TO 'copy'" in statements


def test_prefer_cow_propagates_regular_copy_retry_failure() -> None:
    """Leaves a failed regular-copy retry visible to the caller."""
    connection, _admin, cursor = _database_connection()
    cursor.fetchone.side_effect = [(180000,), (True,)]
    create_attempts = 0

    def execute(statement: object, *_args: object) -> None:
        nonlocal create_attempts
        if "CREATE DATABASE" not in str(statement):
            return
        create_attempts += 1
        if create_attempts == 1:
            message = "clone is unavailable"
            raise psycopg2.errors.FeatureNotSupported(message)
        message = "regular copy failed"
        raise psycopg2.errors.IoError(message)

    cursor.execute.side_effect = execute

    with pytest.raises(psycopg2.errors.IoError, match="regular copy failed"):
        create_database(connection, "template", prefer_cow=True)


@pytest.mark.parametrize(
    "error",
    [psycopg2.errors.DiskFull("disk full"), psycopg2.errors.InsufficientPrivilege("permission denied")],
)
def test_prefer_cow_propagates_clone_errors_unrelated_to_copy_capability(error: Exception) -> None:
    """Does not disguise clone failures that need operator action."""
    connection, _admin, cursor = _database_connection()
    cursor.fetchone.side_effect = [(180000,), (True,)]

    def execute(statement: object, *_args: object) -> None:
        if "CREATE DATABASE" in str(statement):
            raise error

    cursor.execute.side_effect = execute

    with pytest.raises(type(error)):
        create_database(connection, "template", prefer_cow=True)

    assert "SET file_copy_method TO 'copy'" not in _executed_sql(cursor)


def test_prefer_cow_propagates_regular_file_copy_failure() -> None:
    """Does not retry a regular file-copy failure."""
    connection, _admin, cursor = _database_connection()
    cursor.fetchone.side_effect = [(180000,), (False,)]

    def execute(statement: object, *_args: object) -> None:
        if "CREATE DATABASE" in str(statement):
            message = "copy failed"
            raise psycopg2.errors.IoError(message)

    cursor.execute.side_effect = execute

    with pytest.raises(psycopg2.errors.IoError, match="copy failed"):
        create_database(connection, "template", prefer_cow=True)


def test_prefer_cow_propagates_server_errors_and_closes_connection() -> None:
    """Does not hide server failures while choosing a database-copy strategy."""
    connection, admin, cursor = _database_connection()
    cursor.execute.side_effect = RuntimeError("server unavailable")

    with pytest.raises(RuntimeError, match="server unavailable"):
        create_database(connection, "template", prefer_cow=True)

    admin.close.assert_called_once_with()
