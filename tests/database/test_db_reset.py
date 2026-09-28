"""Tests for Odoo-native database lifecycle wrappers."""

import logging
import zipfile
from collections.abc import Sequence
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
    dump_runtime_archive,
    load_legacy_runtime_dump,
    load_runtime_archive,
)
from godoo_cli.runtime.locks import runtime_restore_marker
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
    settings, sources, archives = _development_sources(tmp_path)
    observed: dict[str, Path] = {}

    def require_version(path: Path, _specifier: str) -> OdooVersion:
        observed["version"] = path
        return ODOO19

    def reset_empty(*, odoo_bin_path: Path, **_kwargs: object) -> int:
        observed["bin"] = odoo_bin_path
        return 0

    monkeypatch.setattr(
        "godoo_cli.commands.configuration._development_sources",
        lambda: (settings, sources, archives),
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
    assert observed == {"version": sources[0].host_path, "bin": sources[0].host_path / "odoo-bin"}


@pytest.fixture(autouse=True)
def mock_postgres_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests off PostgreSQL while selecting the Odoo 19 archive path."""
    monkeypatch.setattr(archive, "require_cli_odoo_version", lambda *_args: ODOO19)
    monkeypatch.setattr(runtime_archive, "odoo_bin_get_version", lambda _path: ODOO19)


def test_template_reset_stages_database_and_filestore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that template reset stages database and filestore."""
    commands = []
    monkeypatch.setattr("godoo_cli.runtime.reset.create_database", lambda *_args: None)
    monkeypatch.setattr("godoo_cli.runtime.reset.swap_database", lambda *_args: None)
    monkeypatch.setattr("godoo_cli.runtime.reset.cleanup_database", lambda *_args: None)
    result = reset_runtime_from_template(
        db_name="runtime",
        db_template_name="template",
        odoo_bin_path=Path("/odoo/odoo-bin"),
        odoo_conf_path=Path("/project/odoo.conf"),
        data_dir=tmp_path / "data",
        runner=lambda command: commands.append(command) or 17,
    )
    assert result == 0
    assert commands == []


def test_empty_reset_drops_through_connection_and_preserves_filestore_contract(tmp_path: Path):
    """Guards the contract that empty reset drops through connection and preserves filestore contract."""
    commands = []
    result = reset_empty_runtime(
        db_name="runtime",
        odoo_bin_path=Path("/odoo/odoo-bin"),
        data_dir=tmp_path / "data",
        runner=lambda command: commands.append(command) or 9,
        database_cleaner=lambda _connection: None,
    )
    assert result == 0
    assert commands == []


def test_reset_failed_staged_create_preserves_target_filestore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that reset failed staged create preserves target filestore."""
    data_dir = tmp_path / "data"
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "keep").write_text("old")

    def fail_create(*_args: object) -> None:
        message = "create failed"
        raise RuntimeError(message)

    monkeypatch.setattr("godoo_cli.runtime.reset.create_database", fail_create)
    monkeypatch.setattr("godoo_cli.runtime.reset.cleanup_database", lambda *_args: None)
    with pytest.raises(RuntimeError, match="create failed"):
        reset_runtime_from_template(
            db_name="runtime",
            db_template_name="template",
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=data_dir,
            connection=DBConnection("host", 5432, "user", "pass", "runtime"),
        )
    assert (target / "keep").read_text() == "old"


def test_reset_missing_template_filestore_replaces_target_with_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that reset missing template filestore replaces target with empty."""
    data_dir = tmp_path / "data"
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "obsolete").write_text("old")
    monkeypatch.setattr("godoo_cli.runtime.reset.create_database", lambda *_args: None)
    monkeypatch.setattr("godoo_cli.runtime.reset.swap_database", lambda *_args: None)
    monkeypatch.setattr("godoo_cli.runtime.reset.cleanup_database", lambda *_args: None)
    reset_runtime_from_template(
        db_name="runtime",
        db_template_name="template",
        odoo_bin_path=Path("/odoo/odoo-bin"),
        data_dir=data_dir,
        connection=DBConnection("host", 5432, "user", "pass", "runtime"),
    )
    assert target.is_dir()
    assert not list(target.iterdir())


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
        db_name="runtime",
        odoo_bin_path=Path("/odoo/odoo-bin"),
        data_dir=data_dir,
        connection=DBConnection("host", 5432, "user", "secret", "runtime", sslmode="require"),
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
    dropped: list[str] = []
    monkeypatch.setattr(
        "godoo_cli.runtime.reset.drop_database_strict",
        lambda connection: dropped.append(connection.db_name),
    )

    assert (
        reset_empty_runtime(
            db_name="runtime",
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=data_dir,
            connection=DBConnection("host", 5432, "user", "secret", "runtime"),
        )
        == 0
    )
    assert dropped == ["runtime"]
    assert not target.exists()


def test_omitted_version_uses_pre19_archive_dispatch(monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that omitted version uses pre19 archive dispatch."""
    monkeypatch.setattr(runtime_archive, "odoo_bin_get_version", lambda _path: OdooVersion("Odoo", 18, 0))
    assert runtime_archive._uses_native_db_commands(None, Path("/odoo/odoo-bin")) is False


def test_dump_replaces_destination_only_after_success(tmp_path: Path):
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


def test_native_archive_staging_and_cleanup_use_the_selected_connection(tmp_path: Path):
    """Guards the contract that native archive staging and cleanup use the selected connection."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as runtime_zip:
        runtime_zip.writestr("dump.sql", "select 1;")
    connection = DBConnection("selected-host", 5544, "selected-user", "selected-secret", "runtime", sslmode="require")
    commands: list[list[str]] = []
    cleaned: list[DBConnection] = []

    result = load_runtime_archive(
        db_name="runtime",
        archive_path=archive_path,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        data_dir=tmp_path / "data",
        connection=connection,
        force=True,
        runner=lambda command: commands.append(list(command)) or 13,
        database_cleaner=cleaned.append,
    )

    assert result == 13
    command = commands[0]
    for option, value in (
        ("--db_host", "selected-host"),
        ("--db_port", "5544"),
        ("--db_user", "selected-user"),
        ("--db_password", "selected-secret"),
        ("--db_sslmode", "require"),
    ):
        assert command.index(option) < command.index("load")
        assert command[command.index(option) + 1] == value
    assert cleaned == [connection.with_db(command[-2])]
    assert command[:4] == ["/odoo/odoo-bin", "db", "--data-dir", str(tmp_path / "data")]
    assert command[-3] == "load"
    assert command[-2].startswith("godoo_native_restore_")
    assert command[-1] == str(archive_path)


def test_native_archive_load_clears_inherited_postgres_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Native archive loads must use selected connection PostgreSQL environment."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as runtime_zip:
        runtime_zip.writestr("dump.sql", "select 1;")
    connection = DBConnection("selected-host", 5544, "selected-user", "selected-secret", "runtime", sslmode="require")
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGPASSWORD", "PGSSLMODE"):
        monkeypatch.setenv(name, "hostile")
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        runtime_archive,
        "run_odoo_command",
        lambda _command, **kwargs: observed.update(kwargs) or type("R", (), {"returncode": 13})(),
    )
    monkeypatch.setattr(runtime_archive, "database_exists", lambda *_args: False)
    result = load_runtime_archive(
        db_name="runtime",
        archive_path=archive_path,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        data_dir=tmp_path / "data",
        connection=connection,
        force=True,
        odoo_version=19,
        database_creator=lambda *_args: None,
    )
    assert result == 13
    environment = observed["env"]
    assert isinstance(environment, dict)
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGDATABASE"):
        assert name not in environment
    assert environment["PGPASSWORD"] == "selected-secret"
    assert environment["PGSSLMODE"] == "require"
    assert environment["PSQLRC"]


def test_native_archive_filestore_failure_rolls_back_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that native archive filestore failure rolls back database."""
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as runtime_zip:
        runtime_zip.writestr("dump.sql", "select 1;")
    calls = []

    def fail_swap(_stage: Path, _target: Path) -> None:
        message = "disk unavailable"
        raise OSError(message)

    monkeypatch.setattr(runtime_archive, "replace_filestore", fail_swap)
    with pytest.raises(OSError, match="disk unavailable"):
        load_runtime_archive(
            db_name="runtime",
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=tmp_path / "data",
            force=True,
            runner=lambda _command: 0,
            database_swapper=lambda _connection, _staged: "retained",
            database_rollback=lambda connection, backup: calls.append(("rollback", connection.db_name, backup)),
            database_cleaner=lambda connection: calls.append(("clean", connection.db_name)),
        )
    assert calls[0] == ("rollback", "runtime", "retained")
    assert len(calls) == 1
    assert runtime_restore_marker(tmp_path / "data", "runtime").exists()
    assert list((tmp_path / "data" / "filestore").iterdir())


def test_native_archive_rollback_failure_retains_staging_and_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards native archive cleanup when database rollback is uncertain."""
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
    data_dir = tmp_path / "data"
    with pytest.raises(RuntimeRestoreError, match="pending marker was retained"):
        load_runtime_archive(
            db_name="runtime",
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=data_dir,
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


def test_interrupted_native_archive_keeps_target_and_cleans_staging(tmp_path: Path):
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
        staged = data_dir / "filestore" / command[-2]
        staged.mkdir()
        (staged / "partial").write_text("partial")
        return 143

    result = load_runtime_archive(
        db_name="runtime",
        archive_path=archive_path,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        data_dir=data_dir,
        force=True,
        runner=interrupted,
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
            db_name="runtime",
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=tmp_path / "data",
            force=True,
            runner=lambda command: calls.append(list(command)) or 0,
        )

    assert calls == []


def test_native_archive_existing_target_fails_before_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Reject a non-forced replacement before Odoo creates a staging database."""
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
            db_name="runtime",
            archive_path=archive_path,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            data_dir=tmp_path / "data",
            connection=selected,
            runner=lambda command: calls.append(list(command)) or 0,
        )

    assert calls == []
    assert checked == [selected.with_db("postgres")]


def test_load_database_reports_invalid_native_archive_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
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
    monkeypatch.setattr(archive, "load_runtime_archive", lambda **kwargs: observed.update(kwargs) or 0)
    monkeypatch.setattr(archive, "load_legacy_runtime_dump", lambda **kwargs: observed.update(kwargs))
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

    monkeypatch.setattr(runtime_archive, "restore_custom_runtime", lambda **kwargs: observed.update(kwargs))

    load_legacy_runtime_dump(
        db_name="runtime",
        source_folder=source,
        data_dir=tmp_path / "data",
        db_host="db",
        db_port=5432,
        db_user="odoo",
        db_password="secret",
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
            db_name="runtime",
            source_folder=source,
            data_dir=tmp_path / "data",
            db_host="",
            db_port=0,
            db_user="odoo",
            db_password="",
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
