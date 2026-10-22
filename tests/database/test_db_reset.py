"""Tests for Odoo-native database lifecycle wrappers."""

import logging
import os
import select
import signal
import subprocess
import sys
import zipfile
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

import pytest
from typer.testing import CliRunner

from godoo_cli.commands.db import archive
from godoo_cli.commands.db import reset as reset_command
from godoo_cli.commands.root import main_cli
from godoo_cli.database.connection import DBConnection
from godoo_cli.models import OdooVersion
from godoo_cli.runtime import archive as runtime_archive
from godoo_cli.runtime.archive import (
    RuntimeRestoreError,
    _run_postgres_command,
    dump_runtime_archive,
    load_legacy_runtime_dump,
    load_runtime_archive,
)
from godoo_cli.runtime.locks import runtime_readiness_marker, runtime_restore_marker
from godoo_cli.runtime.reset import reset_empty_runtime, reset_runtime_from_template
from godoo_cli.workspace.types import ResolvedSource, WorkspaceSettings

LOGGER = logging.getLogger(__name__)

ODOO19 = OdooVersion("Odoo", 19, 0)


def _development_sources(tmp_path: Path) -> tuple[WorkspaceSettings, list[ResolvedSource], list[dict[str, str]]]:
    """Return one selected Odoo worktree without source-management writes."""
    selected = tmp_path / "selected-odoo"
    selected.mkdir()
    source = ResolvedSource(
        role="odoo",
        prefix="",
        name="odoo",
        url="https://example.test/odoo.git",
        branch="19.0",
        worktree_branch="godoo/19.0",
        requested_commit="",
        base_commit="base",
        resolved_commit="resolved",
        merge_from=(),
        merge_commits=(),
        recipe_fingerprint="recipe",
        host_path=selected,
        container_path=Path("/odoo/odoo"),
    )
    return WorkspaceSettings(tmp_path, tmp_path / "odoo_manifest.yml", tmp_path / "sources"), [source], []


def test_reset_uses_manifest_selected_odoo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Reset validates and executes the manifest-selected Odoo executable."""
    selected = _development_sources(tmp_path)
    observed: dict[str, Path] = {}

    def require_version(path: Path, _specifier: str) -> OdooVersion:
        observed["version"] = path
        return ODOO19

    def reset_empty(*, data_dir: Path, **_kwargs: object) -> int:
        observed["target"] = data_dir
        return 0

    monkeypatch.setattr(
        "godoo_cli.commands.configuration._development_sources",
        lambda: selected,
    )
    monkeypatch.setattr(
        reset_command,
        "require_cli_odoo_version",
        require_version,
    )
    monkeypatch.setattr(
        reset_command,
        "reset_empty_runtime",
        reset_empty,
    )

    assert (
        reset_command.reset_odoo_state(
            db_name="runtime",
            odoo_main_path=Path("/odoo/odoo"),
            odoo_conf_path=None,
            data_dir=tmp_path / "data",
            empty_reset=True,
        )
        == 0
    )
    assert observed == {"version": selected[1][0].host_path, "target": tmp_path / "data"}


@pytest.fixture(autouse=True)
def mock_postgres_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests off PostgreSQL while selecting the Odoo 19 archive path."""
    monkeypatch.setattr(archive, "require_cli_odoo_version", lambda *_args: ODOO19)
    monkeypatch.setattr(runtime_archive, "odoo_bin_get_version", lambda _path: ODOO19)


def test_template_reset_uses_rsync_and_file_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the explicit-template reset copy contract."""
    commands = []
    source = tmp_path / "data" / "filestore" / "template"
    source.mkdir(parents=True)
    monkeypatch.setattr("godoo_cli.runtime.reset.create_database", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("godoo_cli.runtime.reset.swap_database", lambda *_args: None)
    monkeypatch.setattr("godoo_cli.runtime.reset.cleanup_database", lambda *_args: None)
    result = reset_runtime_from_template(
        connection=DBConnection("", 0, "", "", "runtime"),
        data_dir=tmp_path / "data",
        template="template",
        odoo_conf_path=Path("/project/odoo.conf"),
        runner=lambda command: commands.append(command) or 0,
        database_exists_getter=lambda _connection, _name: True,
    )
    assert result == 0
    assert commands == [
        ["rsync", "-a", "--delete", f"{source}/", str(tmp_path / "data" / "filestore" / "runtime") + "/"]
    ]


def test_empty_reset_drops_through_connection_and_preserves_filestore_contract(tmp_path: Path):
    """Guards the contract that empty reset drops through connection and preserves filestore contract."""
    commands = []
    result = reset_empty_runtime(
        connection=DBConnection("", 0, "", "", "runtime"),
        data_dir=tmp_path / "data",
        database_cleaner=lambda _connection: None,
    )
    assert result == 0
    assert commands == []


def test_reset_failed_staged_create_preserves_target_filestore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that reset failed staged create preserves target filestore."""
    data_dir = tmp_path / "data"
    (data_dir / "filestore" / "template").mkdir(parents=True)
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "keep").write_text("old")

    def fail_create(*_args: object, **_kwargs: object) -> None:
        message = "create failed"
        raise RuntimeError(message)

    monkeypatch.setattr("godoo_cli.runtime.reset.create_database", fail_create)
    monkeypatch.setattr("godoo_cli.runtime.reset.cleanup_database", lambda *_args: None)
    with pytest.raises(RuntimeError, match="create failed"):
        reset_runtime_from_template(
            connection=DBConnection("host", 5432, "user", "pass", "runtime"),
            data_dir=data_dir,
            template="template",
            database_exists_getter=lambda _connection, _name: True,
        )
    assert (target / "keep").read_text() == "old"


def test_reset_missing_template_filestore_preserves_target(tmp_path: Path):
    """Guards the contract that a missing template cannot change the runtime."""
    data_dir = tmp_path / "data"
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "obsolete").write_text("old")
    with pytest.raises(RuntimeError, match="baseline filestore"):
        reset_runtime_from_template(
            connection=DBConnection("host", 5432, "user", "pass", "runtime"),
            data_dir=data_dir,
            template="template",
            database_exists_getter=lambda _connection, _name: True,
        )
    assert (target / "obsolete").read_text() == "old"


@pytest.mark.parametrize("use_custom_cleaner", [True, False], ids=["custom", "default"])
def test_drop_failure_restores_filestore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_custom_cleaner: bool):
    """Guards the contract that drop failure restores filestore."""
    data_dir = tmp_path / "data"
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "keep").write_text("old")

    def fail_drop(_connection: DBConnection) -> None:
        message = "drop failed" if use_custom_cleaner else "postgres unavailable"
        raise RuntimeError(message)

    if not use_custom_cleaner:
        monkeypatch.setattr("godoo_cli.runtime.reset.drop_database_strict", fail_drop)

    result = reset_empty_runtime(
        connection=DBConnection("host", 5432, "user", "secret", "runtime", sslmode="require"),
        data_dir=data_dir,
        database_cleaner=fail_drop if use_custom_cleaner else None,
    )
    assert result == 1
    assert (target / "keep").read_text() == "old"


def test_default_drop_success_removes_filestore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards successful empty reset cleanup with the strict drop path."""
    data_dir = tmp_path / "data"
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "old").write_text("old")
    lifecycle_marker = runtime_readiness_marker(data_dir, "runtime")
    lifecycle_marker.parent.mkdir(parents=True)
    lifecycle_marker.write_text("pending", encoding="utf-8")
    dropped: list[str] = []
    monkeypatch.setattr(
        "godoo_cli.runtime.reset.drop_database_strict",
        lambda connection: dropped.append(connection.db_name),
    )

    assert (
        reset_empty_runtime(connection=DBConnection("host", 5432, "user", "secret", "runtime"), data_dir=data_dir) == 0
    )
    assert dropped == ["runtime"]
    assert not target.exists()
    assert not lifecycle_marker.exists()


@pytest.mark.parametrize(("major", "expected"), [(18, False), (19, True), (20, True), (25, True)])
def test_omitted_version_selects_archive_dispatch(monkeypatch: pytest.MonkeyPatch, major: int, expected: bool):
    """Guards archive dispatch using native commands from Odoo 19 onward."""
    monkeypatch.setattr(runtime_archive, "odoo_bin_get_version", lambda _path: OdooVersion("Odoo", major, 0))
    assert runtime_archive._uses_native_db_commands(None, Path("/odoo/odoo-bin")) is expected


@pytest.mark.parametrize("odoo_version", [19, 20])
def test_dump_replaces_destination_only_after_success(tmp_path: Path, odoo_version: int):
    """Guards the contract that dump replaces destination only after success."""
    destination = tmp_path / "runtime.zip"
    destination.write_bytes(b"previous")

    commands: list[list[str]] = []

    def create_archive(command: Sequence[str]) -> int:
        commands.append(list(command))
        Path(command[-1]).write_bytes(b"new")
        return 0

    assert (
        dump_runtime_archive(
            db_name="runtime",
            archive_path=destination,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=tmp_path / "data",
            odoo_version=odoo_version,
            runner=create_archive,
        )
        == 0
    )
    assert destination.read_bytes() == b"new"
    assert commands[0][:-1] == [
        "/odoo/odoo-bin",
        "db",
        "--data-dir",
        str(tmp_path / "data"),
        "dump",
        "runtime",
    ]
    assert Path(commands[0][-1]).parent == tmp_path
    assert Path(commands[0][-1]).name.startswith(".runtime.zip.")
    assert list(tmp_path.glob("*.zip")) == [destination]
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("odoo_version", [19, 20])
def test_zip_archive_staging_and_cleanup_use_the_selected_connection(tmp_path: Path, odoo_version: int) -> None:
    """Use the selected PostgreSQL connection for ZIP restore staging."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as runtime_zip:
        runtime_zip.writestr("dump.sql", "select 1;")
    connection = DBConnection("selected-host", 5544, "selected-user", "selected-secret", "runtime", sslmode="require")
    commands: list[list[str]] = []
    created: list[DBConnection] = []
    cleaned: list[DBConnection] = []

    result = load_runtime_archive(
        connection=connection,
        data_dir=tmp_path / "data",
        archive_path=archive_path,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        force=True,
        odoo_version=odoo_version,
        runner=lambda command: commands.append(list(command)) or 13,
        database_creator=lambda staged, _template: created.append(staged),
        database_cleaner=cleaned.append,
    )

    assert result == 13
    command = commands[0]
    assert command[:3] == ["psql", "--quiet", "--file"]
    assert Path(command[3]).name == "dump.sql"
    for option, value in (
        ("--host", "selected-host"),
        ("--port", "5544"),
        ("--username", "selected-user"),
    ):
        assert command[command.index(option) + 1] == value
    staged_database = command[command.index("--dbname") + 1]
    assert staged_database.startswith("godoo_native_restore_")
    assert "selected-secret" not in command
    assert created == [connection.with_db(staged_database)]
    assert cleaned == [connection.with_db(staged_database)]


def test_pre_upgrade_archive_load_uses_psql_for_odoo_19(tmp_path: Path):
    """Keep Odoo's registry out of the restore path before pre-upgrade scripts."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as runtime_zip:
        runtime_zip.writestr("dump.sql", "select 1;")
    connection = DBConnection("selected-host", 5544, "selected-user", "selected-secret", "runtime")
    commands: list[list[str]] = []

    result = load_runtime_archive(
        connection=connection,
        data_dir=tmp_path / "data",
        archive_path=archive_path,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        force=True,
        odoo_version=19,
        runner=lambda command: commands.append(list(command)) or 13,
        database_creator=lambda *_args: None,
        database_cleaner=lambda *_args: None,
    )

    assert result == 13
    assert commands[0][0] == "psql"
    assert "--file" in commands[0]


def test_zip_archive_load_clears_inherited_postgres_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run ZIP restore psql with the selected PostgreSQL environment."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as runtime_zip:
        runtime_zip.writestr("dump.sql", "select 1;")
    connection = DBConnection("", 0, "", "selected-secret", "runtime", sslmode="require")
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGPASSWORD", "PGSSLMODE"):
        monkeypatch.setenv(name, "inherited")
    observed: dict[str, str] = {}
    commands: list[str] = []

    def capture_stream(command: Sequence[str], dump_path: Path, environment: dict[str, str]) -> int:
        commands.extend(command)
        assert dump_path.is_file()
        assert "ON_ERROR_STOP" in Path(environment["PSQLRC"]).read_text()
        observed.update(environment)
        return 13

    monkeypatch.setattr(runtime_archive.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runtime_archive, "_stream_sql_input", capture_stream)
    result = load_runtime_archive(
        connection=connection,
        data_dir=tmp_path / "data",
        archive_path=archive_path,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        force=True,
        odoo_version=19,
        database_creator=lambda *_args: None,
    )

    assert result == 13
    assert commands[0] == "psql"
    assert commands[commands.index("--file") + 1] == "-"
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGDATABASE"):
        assert name not in observed
    assert observed["PGPASSWORD"] == "selected-secret"
    assert observed["PGSSLMODE"] == "require"


def test_zip_archive_filestore_failure_rolls_back_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that native archive filestore failure rolls back database."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as runtime_zip:
        runtime_zip.writestr("dump.sql", "select 1;")
    calls = []

    def fail_swap(_stage: Path, _target: Path) -> None:
        message = "disk unavailable"
        raise OSError(message)

    monkeypatch.setattr(runtime_archive, "replace_filestore", fail_swap)
    monkeypatch.setattr(runtime_archive, "_validate_staged_base_version", lambda *_args: None)
    with pytest.raises(OSError, match="disk unavailable"):
        load_runtime_archive(
            connection=DBConnection("", 0, "", "", "runtime"),
            data_dir=tmp_path / "data",
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            force=True,
            runner=lambda _command: 0,
            database_creator=lambda *_args: None,
            database_swapper=lambda _connection, _staged: "retained",
            database_rollback=lambda connection, backup: calls.append(("rollback", connection.db_name, backup)),
            database_cleaner=lambda connection: calls.append(("clean", connection.db_name)),
        )
    assert calls[0] == ("rollback", "runtime", "retained")
    assert len(calls) == 1
    assert runtime_restore_marker(tmp_path / "data", "runtime").exists()
    assert list((tmp_path / "data" / "filestore").iterdir())


def test_zip_archive_rollback_failure_retains_staging_and_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Retain staged ZIP restore data when database rollback is uncertain."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as runtime_zip:
        runtime_zip.writestr("dump.sql", "select 1;")

    def fail_swap(_stage: Path, _target: Path) -> None:
        message = "disk unavailable"
        raise OSError(message)

    def fail_rollback(_connection: DBConnection, _backup: str | None) -> None:
        message = "postgres unavailable"
        raise ConnectionError(message)

    monkeypatch.setattr(runtime_archive, "replace_filestore", fail_swap)
    monkeypatch.setattr(runtime_archive, "_validate_staged_base_version", lambda *_args: None)
    data_dir = tmp_path / "data"
    with pytest.raises(RuntimeRestoreError, match="pending marker was retained"):
        load_runtime_archive(
            connection=DBConnection("", 0, "", "", "runtime"),
            data_dir=data_dir,
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            force=True,
            runner=lambda _command: 0,
            database_creator=lambda *_args: None,
            database_swapper=lambda _connection, _staged: "retained",
            database_rollback=fail_rollback,
            database_cleaner=lambda _connection: pytest.fail("pending staged database must be retained"),
        )

    assert runtime_restore_marker(data_dir, "runtime").exists()
    staged = list((data_dir / "filestore").glob("godoo_native_restore_*"))
    assert len(staged) == 1
    assert staged[0].exists()


def test_interrupted_zip_archive_keeps_target_and_cleans_staging(tmp_path: Path):
    """Guards the contract that interrupted native archive keeps target and cleans staging."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as runtime_zip:
        runtime_zip.writestr("dump.sql", "select 1;")
    data_dir = tmp_path / "data"
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "existing").write_text("keep")
    cleaned = []

    def interrupted(command: Sequence[str]) -> int:
        staged_database = command[command.index("--dbname") + 1]
        staged = data_dir / "filestore" / staged_database
        staged.mkdir()
        (staged / "partial").write_text("partial")
        return 143

    result = load_runtime_archive(
        connection=DBConnection("", 0, "", "", "runtime"),
        data_dir=data_dir,
        archive_path=archive_path,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        force=True,
        runner=interrupted,
        database_creator=lambda *_args: None,
        database_swapper=lambda *_args: pytest.fail("interrupted restore must not replace target"),
        database_cleaner=lambda connection: cleaned.append(connection.db_name),
    )
    assert result == 143
    assert len(cleaned) == 1
    assert cleaned[0].startswith("godoo_native_restore_")
    assert list(target.parent.iterdir()) == [target]
    assert (target / "existing").read_text() == "keep"


def test_invalid_archive_fails_before_forced_load(tmp_path: Path):
    """Guards the contract that invalid archive fails before forced load."""
    archive_path = tmp_path / "invalid.zip"
    archive_path.write_bytes(b"not a zip")
    calls: list[list[str]] = []

    with pytest.raises(RuntimeRestoreError, match="not a valid ZIP"):
        load_runtime_archive(
            connection=DBConnection("", 0, "", "", "runtime"),
            data_dir=tmp_path / "data",
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            force=True,
            runner=lambda command: calls.append(list(command)) or 0,
        )

    assert calls == []


def test_zip_archive_existing_target_fails_before_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Reject a non-forced replacement before creating a staging database."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as runtime_zip:
        runtime_zip.writestr("dump.sql", "select 1;")
    selected = DBConnection("selected-host", 5544, "selected-user", "selected-secret", "runtime")
    checked: list[DBConnection] = []
    monkeypatch.setattr(
        runtime_archive, "database_exists", lambda connection, _name: checked.append(connection) or True
    )
    calls: list[list[str]] = []

    with pytest.raises(RuntimeRestoreError, match="already exists"):
        load_runtime_archive(
            connection=selected,
            data_dir=tmp_path / "data",
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            runner=lambda command: calls.append(list(command)) or 0,
        )

    assert calls == []
    assert checked == [selected.with_db("postgres")]


def test_load_database_reports_invalid_archive_without_a_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that load database reports invalid native archive without a traceback."""
    archive_path = tmp_path / "invalid.zip"
    archive_path.write_bytes(b"not a zip")
    monkeypatch.setattr(archive, "require_cli_odoo_version", lambda *_args: ODOO19)
    monkeypatch.setattr(archive.CLI, "returner", lambda code: code)

    result = archive.load_database(
        db_name="runtime",
        archive_path=archive_path,
        odoo_main_path=Path("/odoo"),
        force=True,
    )

    assert result == 1


@pytest.mark.parametrize("archive_format", ["native", "legacy"])
@pytest.mark.parametrize("selected_by", ["config", "environment", "option"])
def test_load_cli_uses_one_connection_precedence_for_both_archive_formats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    archive_format: str,
    selected_by: str,
):
    """Guards the contract that load cli uses one connection precedence for both archive formats."""
    source = tmp_path / ("legacy" if archive_format == "legacy" else "runtime.zip")
    source.mkdir() if archive_format == "legacy" else source.write_bytes(b"placeholder")
    config_path = tmp_path / "odoo.conf"
    config_path.write_text(
        "[options]\n"
        "db_host = config-host\n"
        "db_port = 5400\n"
        "db_user = config-user\n"
        "db_password = config-secret\n"
        "db_sslmode = prefer\n"
    )
    observed: dict[str, object] = {}
    monkeypatch.setattr(archive, "require_cli_odoo_version", lambda *_args: ODOO19)
    monkeypatch.setattr(
        archive,
        "load_runtime_archive",
        lambda **kwargs: observed.update(**kwargs) or 0,
    )
    monkeypatch.setattr(
        archive,
        "load_legacy_runtime_dump",
        lambda **kwargs: observed.update(**kwargs),
    )
    monkeypatch.chdir(tmp_path)
    for name in (
        "ODOO_DB_HOST",
        "ODOO_DB_PORT",
        "ODOO_DB_USER",
        "ODOO_DB_PASSWORD",
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGSSLMODE",
    ):
        monkeypatch.delenv(name, raising=False)

    environment = {
        "ODOO_MAIN_FOLDER": "/odoo",
        "ODOO_CONF_PATH": str(config_path),
        "ODOO_MAIN_DB": "runtime",
    }
    arguments = ["db", "restore", "--force", str(source)]
    expected = ("config-host", 5400, "config-user", "config-secret", "prefer")
    if selected_by == "environment":
        environment.update(
            PGHOST="environment-host",
            PGPORT="5401",
            PGUSER="environment-user",
            PGPASSWORD="environment-secret",
            PGSSLMODE="require",
        )
        expected = ("environment-host", 5401, "environment-user", "environment-secret", "require")
    elif selected_by == "option":
        arguments[3:3] = [
            "--db-host",
            "option-host",
            "--db-port",
            "5402",
            "--db-user",
            "option-user",
            "--db-password",
            "option-secret",
        ]
        expected = ("option-host", 5402, "option-user", "option-secret", "prefer")

    result = CliRunner().invoke(main_cli(), arguments, env=environment)

    assert result.exit_code == 0, result.output
    connection = observed["connection"]
    assert isinstance(connection, DBConnection)
    assert (
        connection.hostname,
        connection.port,
        connection.username,
        connection.password,
        connection.sslmode,
    ) == expected


def test_load_legacy_dump_selects_the_single_legacy_filestore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that load legacy dump selects the single legacy filestore."""
    source = tmp_path / "legacy"
    dump = source / "odoo.dump"
    filestore = source / "odoo_filestore" / "filestore" / "previous-runtime"
    filestore.mkdir(parents=True)
    dump.write_bytes(b"custom dump")
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        runtime_archive,
        "restore_custom_runtime",
        lambda **kwargs: observed.update(**kwargs),
    )

    load_legacy_runtime_dump(
        connection=DBConnection("db", 5432, "odoo", "secret", "runtime"),
        data_dir=tmp_path / "data",
        source_folder=source,
        db_template="template0",
    )

    assert observed["dump_path"] == dump
    assert observed["filestore_source"] == filestore


def test_load_legacy_dump_rejects_ambiguous_filestores(tmp_path: Path):
    """Guards the contract that load legacy dump rejects ambiguous filestores."""
    source = tmp_path / "legacy"
    (source / "odoo_filestore" / "filestore" / "first").mkdir(parents=True)
    (source / "odoo_filestore" / "filestore" / "second").mkdir()

    with pytest.raises(RuntimeRestoreError, match="unambiguous filestore"):
        load_legacy_runtime_dump(
            connection=DBConnection("", 0, "odoo", "", "runtime"),
            data_dir=tmp_path / "data",
            source_folder=source,
            db_template="template0",
        )


def test_load_legacy_directory_requires_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that load legacy directory requires force."""
    monkeypatch.setattr(archive, "require_cli_odoo_version", lambda *_args: ODOO19)
    monkeypatch.setattr(archive.CLI, "returner", lambda code: code)

    result = archive.load_database(
        db_name="runtime",
        archive_path=tmp_path,
        odoo_main_path=Path("/odoo"),
        force=False,
    )

    assert result == 2


def test_postgres_restore_reports_streamed_sql_bytes_without_pv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """Keeps SQL input progress available when downstream omits pv."""
    dump = tmp_path / "dump.sql"
    dump.write_bytes(b"select 1;\n")
    capture = tmp_path / "captured.sql"
    psql = tmp_path / "psql"
    psql.write_text('#!/bin/sh\ncat > "$GODOO_CAPTURE"\n', encoding="utf-8")
    psql.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GODOO_CAPTURE", str(capture))
    monkeypatch.setattr(runtime_archive.shutil, "which", lambda _command: None)
    caplog.set_level(logging.INFO, logger=runtime_archive.LOGGER.name)

    result = _run_postgres_command(
        ["psql", "--quiet", "--file", str(dump), "--dbname", "staged"],
        DBConnection("host", 5432, "user", "password", "runtime"),
    )

    assert result == 0
    assert capture.read_bytes() == dump.read_bytes()
    assert any("bytes sent (100.0%)" in record.message for record in caplog.records)
    assert any("PostgreSQL restore completed" in record.message for record in caplog.records)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_streamed_sql_input_forwards_shutdown_signals_to_psql(tmp_path: Path, signum: signal.Signals) -> None:
    """Keep the psql fallback aligned with the Odoo command signal contract."""
    dump = tmp_path / "dump.sql"
    dump.write_bytes(b"select 1;\n")
    marker = tmp_path / "signal"
    child_code = (
        "import os, signal, sys\n"
        "from pathlib import Path\n"
        "def stop(signum, _frame):\n"
        "    Path(sys.argv[1]).write_text(f'{os.getpid()}:{signum}')\n"
        "    sys.exit(9)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "signal.signal(signal.SIGINT, stop)\n"
        "print(f'ready:{os.getpid()}', file=sys.stderr, flush=True)\n"
        "signal.pause()\n"
    )
    wrapper_code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from godoo_cli.runtime.archive import _stream_sql_input\n"
        "sys.exit(_stream_sql_input(\n"
        "    [sys.executable, '-c', sys.argv[1], sys.argv[2]], Path(sys.argv[3]), {}\n"
        "))\n"
    )
    wrapper = subprocess.Popen(
        [sys.executable, "-c", wrapper_code, child_code, str(marker), str(dump)],
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid = None
    assert wrapper.stderr is not None
    try:
        assert select.select([wrapper.stderr], [], [], 5)[0]
        label, pid = wrapper.stderr.readline().strip().split(":", 1)
        assert label == "ready"
        child_pid = int(pid)
        os.kill(wrapper.pid, signum)
        assert wrapper.wait(timeout=5) == 9
        marker_pid, received_signal = marker.read_text().split(":")
        assert int(marker_pid) == child_pid
        assert int(received_signal) == signum
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if child_pid is not None:
            with suppress(ProcessLookupError):
                os.killpg(child_pid, signal.SIGKILL)
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait()


def test_postgres_restore_waits_after_consumer_closes_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Returns the consumer failure after its input pipe closes early."""
    dump = tmp_path / "dump.sql"
    dump.write_bytes(b"select 1;\n")

    class ClosedInput:
        def write(self, _data: bytes) -> int:
            raise BrokenPipeError

        def close(self) -> None:
            raise BrokenPipeError

    class FailedPsql:
        stdin = ClosedInput()

        def wait(self) -> int:
            return 7

    monkeypatch.setattr(runtime_archive.subprocess, "Popen", lambda *_args, **_kwargs: FailedPsql())

    assert runtime_archive._stream_sql_input(["psql"], dump, {}) == 7


@pytest.mark.parametrize("odoo_version", [18, 19])
def test_failed_dump_reports_failure_and_preserves_destination(
    tmp_path: Path, odoo_version: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Keep the previous archive and avoid a success message after dump failure."""
    caplog.set_level(logging.INFO, logger=runtime_archive.LOGGER.name)
    destination = tmp_path / "runtime.zip"
    destination.write_bytes(b"previous")

    result = dump_runtime_archive(
        db_name="runtime",
        archive_path=destination,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        data_dir=tmp_path / "data",
        odoo_version=odoo_version,
        runner=lambda _command: 7,
    )

    assert result == 7
    assert destination.read_bytes() == b"previous"
    assert any("Dumping runtime database failed" in record.message for record in caplog.records)
    assert not any("Dumping runtime database completed" in record.message for record in caplog.records)
