"""Tests for strict CoW database duplication."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from godoo_cli.commands.db import cow
from godoo_cli.commands.db.cli import db_cli_app


def test_preflight_requires_postgres_18_and_reports_all_failures(tmp_path: Path):
    result = cow.check_cow_preflight(
        source="same",
        target="same",
        force=False,
        source_database_exists=False,
        target_database_exists=True,
        source_filestore=tmp_path / "missing",
        target_filestore=tmp_path / "target",
        server_version_num=170000,
        file_copy_method="copy",
        reflink_utility_available=False,
    )
    assert not result.available
    assert any("PostgreSQL 18" in error for error in result.errors)
    assert any("file_copy_method" in error for error in result.errors)
    assert any("Source filestore" in error for error in result.errors)
    assert any("Target database" in error for error in result.errors)


def test_preflight_accepts_postgres_18_clone(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    result = cow.check_cow_preflight(
        source="source",
        target="target",
        force=False,
        source_database_exists=True,
        target_database_exists=False,
        source_filestore=source,
        target_filestore=tmp_path / "target",
        server_version_num=180000,
        file_copy_method="clone",
        reflink_utility_available=True,
    )
    assert result.available


def test_database_clone_uses_identifiers_and_file_copy(monkeypatch: pytest.MonkeyPatch):
    executed, identifiers = [], []

    class Cursor:
        _cnx = SimpleNamespace(autocommit=False)

        def execute(self, statement: object) -> None:
            executed.append(statement)

        def close(self) -> None:
            pass

    db_module = ModuleType("odoo.service.db")
    db_module._drop_conn = lambda _cursor, source: executed.append(("disconnect", source))
    db_module.database_identifier = lambda _cursor, name: identifiers.append(name) or f"quoted:{name}"
    service_module = ModuleType("odoo.service")
    service_module.db = db_module
    tools_module = ModuleType("odoo.tools")
    tools_module.SQL = lambda query, *parameters: (query, parameters)
    monkeypatch.setitem(sys.modules, "odoo.service", service_module)
    monkeypatch.setitem(sys.modules, "odoo.service.db", db_module)
    monkeypatch.setitem(sys.modules, "odoo.tools", tools_module)
    database = SimpleNamespace(cursor=lambda: Cursor())
    odoo = SimpleNamespace(
        sql_db=SimpleNamespace(
            close_db=lambda source: executed.append(("close", source)), db_connect=lambda _: database
        )
    )

    cow._create_database_clone(odoo, "source", "target")
    query, parameters = executed[-1]
    assert "STRATEGY FILE_COPY" in query
    assert identifiers == ["target", "source"]
    assert parameters == ("quoted:target", "quoted:source")


def test_reflink_failure_cleans_target_pair(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "source").mkdir()
    removed, operations = [], []
    db_module = ModuleType("odoo.service.db")
    db_module.exp_db_exist = lambda name: name == "source"
    service_module = ModuleType("odoo.service")
    service_module.db = db_module
    tools_module = ModuleType("odoo.tools")
    tools_module.config = SimpleNamespace(filestore=lambda name: tmp_path / name)
    monkeypatch.setitem(sys.modules, "odoo.service", service_module)
    monkeypatch.setitem(sys.modules, "odoo.service.db", db_module)
    monkeypatch.setitem(sys.modules, "odoo.tools", tools_module)
    monkeypatch.setattr(cow, "_server_settings", lambda _: (180000, "clone"))
    monkeypatch.setattr(cow.shutil, "which", lambda _: "/usr/bin/cp")
    monkeypatch.setattr(cow, "_create_database_clone", lambda *_: operations.append("database"))
    monkeypatch.setattr(cow, "_initialize_duplicate_uuid", lambda *_: operations.append("uuid"))
    monkeypatch.setattr(cow, "_remove_target_pair", lambda _, target, path: removed.append((target, path)))

    result = cow.duplicate_cow_runtime(
        source="source",
        target="target",
        force=False,
        odoo_main_path=tmp_path,
        odoo_conf_path=None,
        data_dir=tmp_path,
        db_host="",
        db_port=0,
        db_user="odoo",
        db_password="",
        odoo_loader=lambda **_: SimpleNamespace(),
        reflink_runner=lambda _: (_ for _ in ()).throw(RuntimeError("no reflink")),
    )
    assert result == 1
    assert operations == ["database", "uuid"]
    assert removed == [("target", tmp_path / "target")]


def test_db_cli_registers_duplicate_cow_command():
    assert "duplicate-cow" in [command.name for command in db_cli_app().registered_commands]
