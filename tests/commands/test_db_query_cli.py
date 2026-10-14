"""Regression coverage for the database query command."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from godoo_cli.commands.db import query
from godoo_cli.commands.db.cli import db_cli_app


@pytest.mark.parametrize("readonly_args", [[], ["--readonly"], ["--no-readonly"]])
@pytest.mark.parametrize("sql_args", [["SELECT 1"], ["-"]])
@pytest.mark.parametrize("development_mode", [None, "false"])
def test_query_requires_development_mode_before_reading_or_connecting(
    monkeypatch: pytest.MonkeyPatch,
    readonly_args: list[str],
    sql_args: list[str],
    development_mode: str | None,
) -> None:
    """Queries outside development mode do not inspect input or open a connection."""
    if development_mode is None:
        monkeypatch.delenv("WORKSPACE_IS_DEV", raising=False)
    else:
        monkeypatch.setenv("WORKSPACE_IS_DEV", development_mode)
    monkeypatch.setattr(query.typer, "get_text_stream", lambda _name: pytest.fail("stdin was read"))
    monkeypatch.setattr(query, "_connection", lambda *_args, **_kwargs: pytest.fail("connection was opened"))

    result = CliRunner().invoke(
        db_cli_app(), ["query", *sql_args, "--db-user", "odoo", "--db-name", "demo", *readonly_args]
    )

    assert result.exit_code == 1
    assert "dangerous command" in result.output


@pytest.mark.parametrize(
    ("readonly_args", "expected_readonly"),
    [([], True), (["--readonly"], True), (["--no-readonly"], False)],
)
def test_query_in_development_mode_forwards_readonly_and_prints_rows(
    monkeypatch: pytest.MonkeyPatch, readonly_args: list[str], expected_readonly: bool
) -> None:
    """Development queries preserve readonly forwarding and row output."""
    monkeypatch.setenv("WORKSPACE_IS_DEV", "true")
    observed: dict[str, object] = {}

    class Cursor:
        def execute(self, statement: str) -> None:
            observed["statement"] = statement

        def fetchall(self) -> list[object]:
            return [("first", 1), "second"]

    class Connection:
        def connect(self):
            return CursorContext()

    class CursorContext:
        def __enter__(self) -> Cursor:
            return Cursor()

        def __exit__(self, *_args: object) -> None:
            return None

    def connection(*_args: object, readonly: bool, **_kwargs: object) -> Connection:
        observed["readonly"] = readonly
        return Connection()

    monkeypatch.setattr(query, "_connection", connection)

    result = CliRunner().invoke(
        db_cli_app(), ["query", "SELECT value", "--db-user", "odoo", "--db-name", "demo", *readonly_args]
    )

    assert result.exit_code == 0
    assert observed == {"readonly": expected_readonly, "statement": "SELECT value"}
    assert result.output == "START QUERY_OUTPUT\nfirst\t1\nsecond\nEND QUERY_OUTPUT\n"


def test_query_reads_stdin_after_development_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Development queries accept SQL provided through standard input."""
    monkeypatch.setenv("WORKSPACE_IS_DEV", "true")
    observed: dict[str, object] = {}

    class Cursor:
        def execute(self, statement: str) -> None:
            observed["statement"] = statement

        def fetchall(self) -> list[object]:
            return []

    class Connection:
        def connect(self):
            return CursorContext()

    class CursorContext:
        def __enter__(self) -> Cursor:
            return Cursor()

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(query, "_connection", lambda *_args, **_kwargs: Connection())

    result = CliRunner().invoke(
        db_cli_app(), ["query", "-", "--db-user", "odoo", "--db-name", "demo"], input="SELECT stdin"
    )

    assert result.exit_code == 0
    assert observed == {"statement": "SELECT stdin"}
    assert result.output == "START QUERY_OUTPUT\nEND QUERY_OUTPUT\n"
