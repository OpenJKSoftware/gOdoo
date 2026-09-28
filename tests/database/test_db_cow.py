"""Tests for strict CoW database duplication."""

import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from godoo_cli.database.connection import DBConnection
from godoo_cli.models import GodooConfig
from godoo_cli.runtime import cow
from godoo_cli.runtime.locks import runtime_restore_marker
from godoo_cli.runtime.prepare import _cow_capability_available


def test_preflight_requires_postgres_18_and_reports_all_failures(tmp_path: Path):
    """Guards the contract that preflight requires postgres 18 and reports all failures."""
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
    """Guards the contract that preflight accepts postgres 18 clone."""
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

    source_filestore = tmp_path / "filestore" / "source"
    source_filestore.mkdir(parents=True)
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [(180000,), ("clone",), (True,)]
    db_connection = MagicMock()
    db_connection.with_db.return_value.connect.return_value = cursor
    config = GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        data_dir=tmp_path,
    )
    object.__setattr__(config, "db_connection", db_connection)

    assert _cow_capability_available(config, "source") is True
    db_connection.with_db.assert_called_once_with("postgres", readonly=True)


def test_database_clone_uses_identifiers_and_file_copy(monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that database clone uses identifiers and file copy."""
    executed, identifiers = [], []

    class Cursor:
        _cnx = SimpleNamespace(autocommit=False)

        def execute(self, statement: object) -> None:
            executed.append(statement)

        def close(self) -> None:
            pass

    db_module = ModuleType("odoo.service.db")
    db_module.__dict__.update(
        _drop_conn=lambda _cursor, source: executed.append(("disconnect", source)),
        database_identifier=lambda _cursor, name: identifiers.append(name) or f"quoted:{name}",
    )
    service_module = ModuleType("odoo.service")
    service_module.__dict__.update(db=db_module)
    tools_module = ModuleType("odoo.tools")
    tools_module.__dict__.update(SQL=lambda query, *parameters: (query, parameters))
    monkeypatch.setitem(sys.modules, "odoo.service", service_module)
    monkeypatch.setitem(sys.modules, "odoo.service.db", db_module)
    monkeypatch.setitem(sys.modules, "odoo.tools", tools_module)
    database = SimpleNamespace(cursor=lambda: Cursor())
    odoo = ModuleType("odoo")
    odoo.__dict__.update(
        sql_db=SimpleNamespace(
            close_db=lambda source: executed.append(("close", source)), db_connect=lambda _: database
        )
    )

    cow._create_database_clone(odoo, "source", "target")
    query, parameters = executed[-1]
    assert "STRATEGY FILE_COPY" in query
    assert identifiers == ["target", "source"]
    assert parameters == ("quoted:target", "quoted:source")


def test_reflink_failure_preserves_target_pair(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Guards the contract that a failed staged clone preserves the target pair."""
    (tmp_path / "source").mkdir()
    target_filestore = tmp_path / "target"
    target_filestore.mkdir()
    (target_filestore / "keep").write_text("old")
    operations = []

    class FakeConfig(dict[str, object]):
        def filestore(self, name: str) -> Path:
            return tmp_path / name

    db_module = ModuleType("odoo.service.db")
    db_module.__dict__.update(exp_db_exist=lambda name: name == "source")
    service_module = ModuleType("odoo.service")
    service_module.__dict__.update(db=db_module)
    tools_module = ModuleType("odoo.tools")
    tools_module.__dict__.update(config=FakeConfig(db_host="", db_port=0, db_user="odoo", db_password=""))
    monkeypatch.setitem(sys.modules, "odoo.service", service_module)
    monkeypatch.setitem(sys.modules, "odoo.service.db", db_module)
    monkeypatch.setitem(sys.modules, "odoo.tools", tools_module)
    monkeypatch.setattr(cow, "_server_settings", lambda _: (180000, "clone"))
    monkeypatch.setattr(cow.shutil, "which", lambda _: "/usr/bin/cp")
    monkeypatch.setattr(cow, "_create_database_clone", lambda *_: operations.append("database"))
    monkeypatch.setattr(cow, "_initialize_duplicate_uuid", lambda *_: operations.append("uuid"))

    result = cow.duplicate_cow_runtime(
        source="source",
        target="target",
        force=True,
        odoo_main_path=tmp_path,
        odoo_conf_path=None,
        data_dir=tmp_path,
        db_host="",
        db_port=0,
        db_user="odoo",
        db_password="",
        odoo_loader=lambda **_: ModuleType("odoo"),
        reflink_runner=lambda _: (_ for _ in ()).throw(RuntimeError("no reflink")),
    )
    assert result == 1
    assert operations == ["database", "uuid"]
    assert (target_filestore / "keep").read_text() == "old"


def test_clone_promotion_uses_loaded_odoo_database_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Use the same configured PostgreSQL endpoint for cloning and promotion."""
    source_filestore = tmp_path / "source"
    source_filestore.mkdir()

    class FakeConfig(dict[str, object]):
        def filestore(self, name: str) -> Path:
            return tmp_path / name

    config = FakeConfig(
        db_host="configured-host",
        db_port=5544,
        db_user="configured-user",
        db_password="configured-secret",
    )
    db_module = ModuleType("odoo.service.db")
    db_module.__dict__.update(exp_db_exist=lambda name: name == "source")
    service_module = ModuleType("odoo.service")
    service_module.__dict__.update(db=db_module)
    tools_module = ModuleType("odoo.tools")
    tools_module.__dict__.update(config=config)
    monkeypatch.setitem(sys.modules, "odoo.service", service_module)
    monkeypatch.setitem(sys.modules, "odoo.service.db", db_module)
    monkeypatch.setitem(sys.modules, "odoo.tools", tools_module)
    monkeypatch.setattr(cow, "_server_settings", lambda _: (180000, "clone"))
    monkeypatch.setattr(cow.shutil, "which", lambda _: "/usr/bin/cp")
    monkeypatch.setattr(cow, "_create_database_clone", lambda *_: None)
    monkeypatch.setattr(cow, "_initialize_duplicate_uuid", lambda *_: None)
    observed: list[DBConnection] = []

    def copy_filestore(command: Sequence[str]) -> None:
        destination = Path(command[-1])
        destination.mkdir()
        (destination / "copied").write_text("new")

    def swap_database(connection: DBConnection, _staged_database: str) -> None:
        observed.append(connection)

    monkeypatch.setattr(cow, "swap_database", swap_database)

    result = cow.duplicate_cow_runtime(
        source="source",
        target="target",
        force=False,
        odoo_main_path=tmp_path,
        odoo_conf_path=None,
        data_dir=tmp_path,
        db_host="",
        db_port=0,
        db_user="",
        db_password="",
        odoo_loader=lambda **_: ModuleType("odoo"),
        reflink_runner=copy_filestore,
    )

    assert result == 0
    assert observed == [DBConnection("configured-host", 5544, "configured-user", "configured-secret", "target")]
    assert (tmp_path / "target" / "copied").read_text() == "new"


def test_promotion_rollback_failure_retains_cow_recovery_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Retain the marker and staged pair when database rollback is uncertain."""
    source_filestore = tmp_path / "source"
    source_filestore.mkdir()
    target_filestore = tmp_path / "target"
    target_filestore.mkdir()
    (target_filestore / "keep").write_text("old")

    class FakeConfig(dict[str, object]):
        def filestore(self, name: str) -> Path:
            return tmp_path / name

    config = FakeConfig(db_host="postgres", db_port=5432, db_user="odoo", db_password="secret")
    db_module = ModuleType("odoo.service.db")
    db_module.__dict__.update(exp_db_exist=lambda name: name in {"source", "target"})
    service_module = ModuleType("odoo.service")
    service_module.__dict__.update(db=db_module)
    tools_module = ModuleType("odoo.tools")
    tools_module.__dict__.update(config=config)
    monkeypatch.setitem(sys.modules, "odoo.service", service_module)
    monkeypatch.setitem(sys.modules, "odoo.service.db", db_module)
    monkeypatch.setitem(sys.modules, "odoo.tools", tools_module)
    monkeypatch.setattr(cow, "_server_settings", lambda _: (180000, "clone"))
    monkeypatch.setattr(cow.shutil, "which", lambda _: "/usr/bin/cp")
    monkeypatch.setattr(cow, "_create_database_clone", lambda *_: None)
    monkeypatch.setattr(cow, "_initialize_duplicate_uuid", lambda *_: None)
    monkeypatch.setattr(cow, "swap_database", lambda *_: "old_backup")
    cleaned: list[str] = []
    staged_filestores: list[Path] = []

    def copy_filestore(command: Sequence[str]) -> None:
        destination = Path(command[-1])
        destination.mkdir()
        (destination / "copied").write_text("new")
        staged_filestores.append(destination)

    def fail_filestore_swap(_stage: Path, _target: Path) -> None:
        message = "filestore unavailable"
        raise OSError(message)

    def fail_database_rollback(_connection: DBConnection, _backup: str | None) -> None:
        message = "postgres unavailable"
        raise ConnectionError(message)

    monkeypatch.setattr("godoo_cli.runtime.promotion.replace_filestore", fail_filestore_swap)
    monkeypatch.setattr(cow, "rollback_database_swap", fail_database_rollback)
    monkeypatch.setattr(cow, "cleanup_database", lambda connection: cleaned.append(connection.db_name))

    result = cow.duplicate_cow_runtime(
        source="source",
        target="target",
        force=True,
        odoo_main_path=tmp_path,
        odoo_conf_path=None,
        data_dir=tmp_path,
        db_host="",
        db_port=0,
        db_user="",
        db_password="",
        odoo_loader=lambda **_: ModuleType("odoo"),
        reflink_runner=copy_filestore,
    )

    assert result == 1
    assert runtime_restore_marker(tmp_path, "target").exists()
    assert staged_filestores[0].exists()
    assert (staged_filestores[0] / "copied").read_text() == "new"
    assert (target_filestore / "keep").read_text() == "old"
    assert cleaned == []
