"""Unit tests for additive Odoo dependency preflight."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypedDict
from unittest.mock import patch

import psycopg2
import pytest

from godoo_cli.commands.run import run_odoo
from godoo_cli.database.state import DbBootstrapStatus
from godoo_cli.models import GodooConfig, OdooVersion
from godoo_cli.runtime.locks import runtime_readiness_marker, runtime_restore_marker
from godoo_cli.runtime.odoo import (
    dependency_requirements,
    preflight_for_config,
    require_runtime_database_major,
    require_runtime_launch_ready,
    resolve_odoo_config,
    sanitize_odoo_password,
    selected_modules,
)


@pytest.fixture(autouse=True)
def skip_database_major_guard_for_dependency_preflight_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep dependency tests independent of a live configured Odoo database."""
    monkeypatch.setattr("godoo_cli.runtime.odoo.require_runtime_database_major", lambda *_args: None)


class FakeCursor:
    def __init__(
        self,
        rows: list[tuple[str, ...]] | None = None,
        maintenance_rows: list[tuple[bool, ...]] | None = None,
        error: Exception | None = None,
        imported_rows: list[tuple[str, ...]] | None = None,
        has_imported_column: bool = False,
    ) -> None:
        self.rows = rows or []
        self.maintenance_rows = maintenance_rows or []
        self.error = error
        self.imported_rows = imported_rows or []
        self.has_imported_column = has_imported_column
        self.query = ""

    def execute(self, query: str, *_args: object) -> None:
        self.query = query
        if self.error:
            raise self.error

    def fetchall(self) -> list[tuple[str, ...]]:
        if "WHERE imported" in self.query:
            return self.imported_rows
        return self.rows

    def fetchone(self) -> tuple[str | bool, ...] | None:
        if "information_schema.columns" in self.query:
            return (self.has_imported_column,)
        if "pg_database" in self.query:
            return self.maintenance_rows[0] if self.maintenance_rows else None
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(
        self,
        rows: list[tuple[str, ...]] | None = None,
        error: Exception | None = None,
        maintenance_rows: list[tuple[bool, ...]] | None = None,
        maintenance_error: Exception | None = None,
        imported_rows: list[tuple[str, ...]] | None = None,
        has_imported_column: bool = False,
    ) -> None:
        self.rows = rows or []
        self.error = error
        self.maintenance_rows = maintenance_rows
        self.maintenance_error = maintenance_error
        self.imported_rows = imported_rows
        self.has_imported_column = has_imported_column
        self.names: list[str] = []

    def with_db(self, name: str, *, readonly: bool | None = None) -> FakeConnection:
        del readonly
        child = FakeConnection(
            rows=self.rows,
            error=self.maintenance_error if name == "postgres" else self.error,
            maintenance_rows=self.maintenance_rows,
            maintenance_error=self.maintenance_error,
            imported_rows=self.imported_rows,
            has_imported_column=self.has_imported_column,
        )
        child.names = [name]
        return child

    def connect(self) -> Any:
        cursor = FakeCursor(
            self.rows,
            self.maintenance_rows,
            self.error,
            self.imported_rows,
            self.has_imported_column,
        )

        class Context:
            def __enter__(self) -> FakeCursor:
                return cursor

            def __exit__(self, *_args: object) -> None:
                return None

        return Context()


class OdooCommandPaths(TypedDict):
    """Required filesystem arguments shared by Odoo command tests."""

    odoo_main_path: Path
    workspace_addon_path: Path
    odoo_conf_path: Path


class MultiDatabaseConnection:
    """Return database-specific cursors for multi-database selection tests."""

    def __init__(self, databases: dict[str, FakeConnection]) -> None:
        self.databases = databases

    def with_db(self, name: str, *, readonly: bool | None = None) -> FakeConnection:
        del readonly
        return self.databases[name]


def manifest(root: Path, name: str, depends: list[str] | None = None, python: list[str] | None = None) -> None:
    path = root / name
    path.mkdir(parents=True)
    (path / "__manifest__.py").write_text(
        repr({"name": name, "depends": depends or [], "external_dependencies": {"python": python or []}})
    )


def config(tmp_path: Path) -> GodooConfig:
    odoo = tmp_path / "odoo"
    addons = odoo / "addons"
    addons.mkdir(parents=True)
    manifest(addons, "base")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return GodooConfig(
        odoo_install_folder=odoo,
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=workspace,
        thirdparty_addon_path=tmp_path / "thirdparty",
        db_name="db-a",
    )


def test_fresh_base_and_requested_modules_resolve_recursive_dependencies(tmp_path: Path):
    """Guards the contract that fresh base and requested modules resolve recursive dependencies."""
    cfg = config(tmp_path)
    manifest(cfg.workspace_addon_path, "dep", python=["dep-pkg"])
    manifest(cfg.workspace_addon_path, "sale", ["dep"])
    object.__setattr__(cfg, "db_connection", FakeConnection())
    assert selected_modules(cfg, ["-i", "sale"]) == ["base", "sale", "dep"]
    assert dependency_requirements(cfg, ["-i", "sale"]) == ["dep-pkg"]


def test_update_all_uses_modules_installed_in_database_only(tmp_path: Path):
    """Guards the contract that update all uses modules installed in database only."""
    cfg = config(tmp_path)
    manifest(cfg.workspace_addon_path, "sale")
    manifest(cfg.workspace_addon_path, "uninstalled")
    object.__setattr__(cfg, "db_connection", FakeConnection([("base",), ("sale",)]))
    assert selected_modules(cfg, ["-u", "all"]) == ["base", "sale"]


def test_virtual_studio_module_does_not_require_an_addon_source(tmp_path: Path):
    """Guards Odoo's native virtual Studio module behavior."""
    cfg = config(tmp_path)
    object.__setattr__(cfg, "db_connection", FakeConnection([("base",), ("studio_customization",)]))

    assert selected_modules(cfg, ["-u", "studio_customization"]) == ["base"]


def test_imported_database_module_without_source_is_skipped(tmp_path: Path):
    """Guards Odoo 19 imported modules without an addon manifest."""
    cfg = config(tmp_path)
    object.__setattr__(
        cfg,
        "db_connection",
        FakeConnection(
            [("base",), ("imported_customization",)],
            imported_rows=[("imported_customization",)],
            has_imported_column=True,
        ),
    )

    assert selected_modules(cfg) == ["base"]


def test_missing_ordinary_database_module_still_raises(tmp_path: Path):
    """Guards missing installed addon sources remain preflight errors."""
    cfg = config(tmp_path)
    object.__setattr__(cfg, "db_connection", FakeConnection([("base",), ("missing_module",)]))

    with pytest.raises(ModuleNotFoundError, match="missing_module"):
        selected_modules(cfg)


def test_pre_upgrade_preflight_skips_missing_installed_module(tmp_path: Path):
    """Allow a pre-upgrade script to reconcile a legacy installed module."""
    cfg = config(tmp_path)
    object.__setattr__(cfg, "db_connection", FakeConnection([("base",), ("legacy_module",)]))

    assert dependency_requirements(cfg, ignore_missing_installed_modules=True) == []


def test_imported_module_in_one_database_does_not_hide_other_database_missing_source(tmp_path: Path):
    """Guards imported metadata stays specific to its database."""
    cfg = GodooConfig(**{**config(tmp_path).__dict__, "db_name": "first,second"})
    object.__setattr__(
        cfg,
        "db_connection",
        MultiDatabaseConnection(
            {
                "first": FakeConnection(
                    [("base",), ("shared_module",)],
                    imported_rows=[("shared_module",)],
                    has_imported_column=True,
                ),
                "second": FakeConnection([("base",), ("shared_module",)]),
            }
        ),
    )

    with pytest.raises(ModuleNotFoundError, match="shared_module"):
        selected_modules(cfg)


def test_ldap_manifest_dependency_uses_python_ldap_distribution(tmp_path: Path):
    """Guards legacy Odoo import names become uv distributions."""
    cfg = config(tmp_path)
    manifest(cfg.workspace_addon_path, "auth_ldap", python=["ldap", "python-ldap"])
    object.__setattr__(cfg, "db_connection", FakeConnection())

    assert dependency_requirements(cfg, ["-i", "auth_ldap"]) == ["python-ldap"]


def test_multiple_databases_are_unioned(tmp_path: Path):
    """Guards the contract that multiple databases are unioned."""
    cfg = config(tmp_path)
    manifest(cfg.workspace_addon_path, "sale")
    cfg = GodooConfig(**{**cfg.__dict__, "db_name": "a,b"})
    connection = FakeConnection([("base",), ("sale",)])
    object.__setattr__(cfg, "db_connection", connection)
    assert selected_modules(cfg) == ["base", "sale"]


def test_command_config_supplies_database_and_addon_defaults(tmp_path: Path):
    """Config-file values drive both preflight database access and addon discovery."""
    cfg = config(tmp_path)
    extra = tmp_path / "extra"
    extra.mkdir()
    conf = tmp_path / "selected.conf"
    conf.write_text(
        f"[options]\ndb_name = configured\ndb_user = configured\ndb_password = secret\ndb_host = postgres\ndb_port = 5433\naddons_path = {extra},{cfg.workspace_addon_path}\n"
    )
    result = resolve_odoo_config(cfg, ["-c", str(conf)])
    assert (result.db_name, result.db_user, result.db_password, result.db_host, result.db_port) == (
        "configured",
        "configured",
        "secret",
        "postgres",
        5433,
    )
    assert result.workspace_addon_path == cfg.workspace_addon_path
    assert result.thirdparty_addon_path == cfg.thirdparty_addon_path
    assert result.resolved_addon_paths == (extra, cfg.workspace_addon_path)


def test_direct_database_arguments_override_config_including_empty_and_zero(tmp_path: Path):
    """Explicit empty host and zero port remain stronger than file defaults."""
    cfg = config(tmp_path)
    conf = tmp_path / "stale.conf"
    conf.write_text("[options]\ndb_name = configured-db\ndb_host = configured-host\ndb_port = 5433\n")
    result = resolve_odoo_config(cfg, ["-c", str(conf), "--db-host=", "--db-port", "0", "-d=argv-db"])
    assert (result.db_name, result.db_host, result.db_port) == ("argv-db", "", 0)


def test_config_sentinel_values_leave_direct_database_overrides_intact(tmp_path: Path):
    """Generated ``None`` and ``False`` connection sentinels are not parsed as ports."""
    cfg = config(tmp_path)
    conf = tmp_path / "generated.conf"
    conf.write_text("[options]\ndb_host = False\ndb_port = None\n")

    resolved = resolve_odoo_config(
        cfg,
        ["--config", str(conf)],
        direct={"db_host": "selected-host", "db_port": 0},
    )

    assert (resolved.db_host, resolved.db_port) == ("selected-host", 0)


def test_preflight_debug_logs_discovery_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """Expose preflight discovery phases without asserting elapsed values."""
    cfg = config(tmp_path)
    manifest(cfg.workspace_addon_path, "sale", python=["sale-pkg"])
    object.__setattr__(cfg, "db_connection", FakeConnection())
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "venv"))
    monkeypatch.setattr("godoo_cli.runtime.odoo.execution_python", lambda _project_root: Path(sys.executable))
    monkeypatch.setattr("godoo_cli.runtime.odoo.subprocess.run", lambda *_args, **_kwargs: None)

    with caplog.at_level(logging.DEBUG, logger="godoo_cli.runtime.odoo"):
        preflight_for_config(cfg, ["--init", "sale"])

    messages = [record.getMessage() for record in caplog.records]
    expected_boundaries = [
        "configuration resolved:",
        "command options parsed:",
        "database module discovery completed:",
        "module registry constructed: phase=selection",
        "selected module resolution completed:",
        "module dependency closure completed:",
        "module registry constructed: phase=manifest",
        "external dependency manifest discovery completed:",
        "dependency discovery completed:",
        "dependency installation completed:",
    ]
    for boundary in expected_boundaries:
        message = next(message for message in messages if boundary in message)
        assert "elapsed=" in message


def test_preflight_uses_the_supplied_effective_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Preflight preserves direct selection already resolved by its command adapter."""
    cfg = config(tmp_path)
    effective = resolve_odoo_config(cfg, direct={"db_host": "selected-host", "db_port": 0})
    observed: list[GodooConfig] = []
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.dependency_requirements",
        lambda config, *_args, **_kwargs: observed.append(config) or [],
    )
    monkeypatch.setattr("godoo_cli.runtime.odoo.subprocess.run", lambda *_args, **_kwargs: None)

    preflight_for_config(effective, project_root=tmp_path)

    assert observed == [effective]


def test_explicit_command_layer_values_override_config(tmp_path: Path):
    """CLI values are applied after config and forwarded Odoo options."""
    cfg = config(tmp_path)
    conf = tmp_path / "stale.conf"
    conf.write_text("[options]\ndb_user = configured\ndb_password = configured-secret\n")
    result = resolve_odoo_config(
        cfg, ["-c", str(conf), "--db_user=argv-user"], direct={"db_user": "cli-user", "db_password": ""}
    )
    assert (result.db_user, result.db_password) == ("cli-user", "")


def test_config_addon_paths_preserve_every_root(tmp_path: Path):
    """All configured addon roots participate in dependency discovery."""
    cfg = GodooConfig(**{**config(tmp_path).__dict__, "db_name": ""})
    roots = [tmp_path / f"addons-{index}" for index in range(3)]
    manifest(roots[-1], "base")
    manifest(roots[-1], "late_module", python=["late-pkg"])
    cfg.odoo_conf_path.write_text(f"[options]\naddons_path = {','.join(str(root) for root in roots)}\n")

    effective = resolve_odoo_config(cfg)

    assert effective.resolved_addon_paths == tuple(roots)
    assert dependency_requirements(cfg, ["--init", "late_module"]) == ["late-pkg"]


def test_config_only_addons_path_is_used_for_manifest_discovery(tmp_path: Path):
    """Guards the contract that config only addons path is used for manifest discovery."""
    cfg = config(tmp_path)
    cfg = GodooConfig(**{**cfg.__dict__, "db_name": ""})
    addon_root = tmp_path / "configured-addons"
    manifest(addon_root, "base")
    manifest(addon_root, "configured_module", python=["configured-pkg"])
    cfg.odoo_conf_path.write_text(f"[options]\naddons_path = {addon_root}\n")
    assert selected_modules(cfg, ["-i", "configured_module"]) == ["base", "configured_module"]
    assert dependency_requirements(cfg, ["-i", "configured_module"]) == ["configured-pkg"]


def test_missing_requested_manifest_raises(tmp_path: Path):
    """Guards the contract that missing requested manifest raises."""
    cfg = config(tmp_path)
    object.__setattr__(cfg, "db_connection", FakeConnection())
    with pytest.raises(ModuleNotFoundError):
        selected_modules(cfg, ["-i", "missing"])


def test_missing_transitive_manifest_fails_before_uv_install(tmp_path: Path):
    """Guards the contract that missing transitive manifest fails before uv install."""
    cfg = config(tmp_path)
    manifest(cfg.workspace_addon_path, "sale", ["missing_dependency"])
    object.__setattr__(cfg, "db_connection", FakeConnection())
    with patch("godoo_cli.runtime.odoo.subprocess.run") as run, pytest.raises(ModuleNotFoundError):
        preflight_for_config(cfg, ["-i", "sale"])
    run.assert_not_called()


def test_cyclic_dependencies_terminate(tmp_path: Path):
    """Guards the contract that cyclic dependencies terminate."""
    cfg = config(tmp_path)
    manifest(cfg.workspace_addon_path, "first", ["second"])
    manifest(cfg.workspace_addon_path, "second", ["first"])
    object.__setattr__(cfg, "db_connection", FakeConnection())
    assert selected_modules(cfg, ["-i", "first"]) == ["base", "first", "second"]


def test_database_auth_failure_propagates(tmp_path: Path):
    """Guards the contract that database auth failure propagates."""
    cfg = config(tmp_path)
    object.__setattr__(cfg, "db_connection", FakeConnection(error=RuntimeError("authentication failed")))
    with pytest.raises(RuntimeError, match="authentication failed"):
        selected_modules(cfg)


def test_missing_database_operational_error_allows_fresh_modules(tmp_path: Path):
    """Guards missing database confirmation before fresh module selection."""
    cfg = config(tmp_path)
    manifest(cfg.workspace_addon_path, "sale")
    object.__setattr__(
        cfg,
        "db_connection",
        FakeConnection(error=psycopg2.OperationalError("database missing"), maintenance_rows=[(False,)]),
    )
    assert selected_modules(cfg, ["-i", "sale"]) == ["base", "sale"]


@pytest.mark.parametrize("maintenance_rows", [[(True,)], []])
def test_target_operational_error_is_preserved_without_confirmed_missing_database(
    tmp_path: Path, maintenance_rows: list[tuple[bool, ...]]
):
    """Keep the target error when the maintenance catalog is inconclusive."""
    cfg = config(tmp_path)
    target_error = psycopg2.OperationalError("target unavailable")
    object.__setattr__(
        cfg,
        "db_connection",
        FakeConnection(error=target_error, maintenance_rows=maintenance_rows),
    )

    with pytest.raises(psycopg2.OperationalError) as raised:
        selected_modules(cfg)

    assert raised.value is target_error


def test_target_operational_error_is_preserved_when_maintenance_query_fails(tmp_path: Path):
    """Keep the target error when PostgreSQL cannot confirm database absence."""
    cfg = config(tmp_path)
    target_error = psycopg2.OperationalError("target unavailable")
    object.__setattr__(
        cfg,
        "db_connection",
        FakeConnection(
            error=target_error,
            maintenance_error=psycopg2.OperationalError("maintenance unavailable"),
        ),
    )

    with pytest.raises(psycopg2.OperationalError) as raised:
        selected_modules(cfg)

    assert raised.value is target_error


def test_preflight_passes_requirements_file_to_uv_and_skips_help(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that preflight passes requirements file to uv and skips help."""
    cfg = config(tmp_path)
    (cfg.odoo_install_folder / "requirements.txt").write_text("-r base.txt\n")
    monkeypatch.setattr("godoo_cli.runtime.odoo.dependency_requirements", lambda *_args, **_kwargs: ["addon-pkg"])
    with patch("godoo_cli.runtime.odoo.subprocess.run") as run:
        preflight_for_config(cfg, ["--update", "sale"])
        assert "-r" in run.call_args.args[0]
        run.reset_mock()
        preflight_for_config(cfg, ["--help"])
        run.assert_not_called()


def test_existing_project_environment_is_never_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that existing project environment is never replaced."""
    cfg = config(tmp_path)
    (tmp_path / ".venv").mkdir()
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr("godoo_cli.runtime.odoo.dependency_requirements", lambda *_args, **_kwargs: ["pkg"])
    with patch("godoo_cli.runtime.odoo.subprocess.run") as run:
        with pytest.raises(FileNotFoundError):
            preflight_for_config(cfg, project_root=tmp_path)
        run.assert_not_called()


def test_virtual_environment_wins_over_project_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that virtual environment wins over project environment."""
    cfg = config(tmp_path)
    virtual = tmp_path / "runtime"
    (virtual / "bin").mkdir(parents=True)
    (virtual / "bin" / "python").touch()
    monkeypatch.setenv("VIRTUAL_ENV", str(virtual))
    monkeypatch.setattr("godoo_cli.runtime.odoo.dependency_requirements", lambda *_args, **_kwargs: ["pkg"])
    with patch("godoo_cli.runtime.odoo.subprocess.run") as run:
        preflight_for_config(cfg)
    assert str(virtual / "bin" / "python") in run.call_args.args[0]


def test_preflight_skips_core_requirements_preinstalled_by_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Container images install core requirements once during their build."""
    cfg = config(tmp_path)
    (cfg.odoo_install_folder / "requirements.txt").write_text("odoo-core\n", encoding="utf-8")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "runtime"))
    monkeypatch.setenv("GODOO_ODOO_REQUIREMENTS_PREINSTALLED", "1")
    monkeypatch.setattr("godoo_cli.runtime.odoo.execution_python", lambda *_args: Path(sys.executable))
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.dependency_requirements",
        lambda *_args, **_kwargs: ["addon-package"],
    )

    with patch("godoo_cli.runtime.odoo.subprocess.run") as run:
        preflight_for_config(cfg)

    command = run.call_args.args[0]
    assert "-r" not in command
    assert "addon-package" in command


def test_run_forwards_native_command_without_server_configuration(tmp_path: Path):
    """Native Odoo commands use the resolved executable and retain their argv."""
    cfg = config(tmp_path)
    paths: OdooCommandPaths = {
        "odoo_main_path": cfg.odoo_install_folder,
        "workspace_addon_path": cfg.workspace_addon_path,
        "odoo_conf_path": cfg.odoo_conf_path,
    }
    arguments = ["scaffold", "demo"]
    with (
        patch("godoo_cli.commands.run.preflight_for_config") as preflight,
        patch("godoo_cli.commands.run.run_odoo_command", return_value=SimpleNamespace(returncode=0)) as run,
    ):
        assert run_odoo(arguments, **paths) == 0

    preflight.assert_called_once()
    assert preflight.call_args.args[0].odoo_bin_path == cfg.odoo_bin_path
    assert preflight.call_args.args[1] == arguments
    assert preflight.call_args.kwargs == {"include_module_dependencies": False}
    assert run.call_args.args[0] == [str(cfg.odoo_bin_path), *arguments]


def test_native_preflight_installs_core_requirements_without_module_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Native commands prepare Odoo requirements without resolving addons or databases."""
    cfg = config(tmp_path)
    (cfg.odoo_install_folder / "requirements.txt").write_text("odoo-core\n")
    virtual = tmp_path / "virtual"
    (virtual / "bin").mkdir(parents=True)
    (virtual / "bin" / "python").touch()
    monkeypatch.setenv("VIRTUAL_ENV", str(virtual))
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.dependency_requirements",
        lambda *_args, **_kwargs: pytest.fail("native preflight must not resolve modules"),
    )
    with patch("godoo_cli.runtime.odoo.subprocess.run") as run:
        preflight_for_config(cfg, ["scaffold", "demo"], include_module_dependencies=False)

    assert "-r" in run.call_args.args[0]
    assert str(cfg.odoo_install_folder / "requirements.txt") in run.call_args.args[0]


def test_run_emits_resolved_data_dir_for_managed_server(tmp_path: Path):
    """Managed server commands emit their selected data directory once."""
    cfg = config(tmp_path)
    paths: OdooCommandPaths = {
        "odoo_main_path": cfg.odoo_install_folder,
        "workspace_addon_path": cfg.workspace_addon_path,
        "odoo_conf_path": cfg.odoo_conf_path,
    }
    selected_data_dir = tmp_path / "selected-data"
    with (
        patch("godoo_cli.commands.run.preflight_for_config"),
        patch("godoo_cli.commands.run.run_odoo_command", return_value=SimpleNamespace(returncode=0)) as run,
    ):
        assert run_odoo(["server"], data_dir=selected_data_dir, **paths) == 0

    command = run.call_args.args[0]
    assert command.count("--data-dir") == 1
    assert command[command.index("--data-dir") + 1] == str(selected_data_dir.absolute())
    assert "/var/lib/odoo" not in command


def test_run_inserts_resolved_options_before_argument_terminator(tmp_path: Path):
    """Resolved credentials remain Odoo options and are sanitized before ``--``."""
    cfg = config(tmp_path)
    paths: OdooCommandPaths = {
        "odoo_main_path": cfg.odoo_install_folder,
        "workspace_addon_path": cfg.workspace_addon_path,
        "odoo_conf_path": cfg.odoo_conf_path,
    }
    arguments = ["--stop-after-init", "--", "opaque-password-argument"]
    with (
        patch("godoo_cli.commands.run.preflight_for_config"),
        patch("godoo_cli.commands.run.run_odoo_command", return_value=SimpleNamespace(returncode=0)) as run,
    ):
        assert run_odoo(arguments, db_password="selected-secret", **paths) == 0

    command = run.call_args.args[0]
    assert command.index("--db_password") < command.index("--")
    assert command[-2:] == ["--", "opaque-password-argument"]
    sanitized, environment = sanitize_odoo_password(command)
    assert "selected-secret" not in sanitized
    assert environment["PGPASSWORD"] == "selected-secret"


@pytest.mark.parametrize(
    ("arguments", "password"),
    [
        (["-w", "secret"], "secret"),
        (["-wsecret"], "secret"),
        (["--db_password", "secret"], "secret"),
        (["--db-password=secret"], "secret"),
        (["--db_password="], ""),
    ],
)
def test_password_options_move_secrets_to_environment(arguments: list[str], password: str) -> None:
    """All supported password forms leave one canonical empty option."""
    sanitized, environment = sanitize_odoo_password(arguments, {})
    assert sanitized == ["--db_password="]
    assert environment["PGPASSWORD"] == password


def test_password_options_after_separator_remain_opaque() -> None:
    """Odoo receives literal arguments after its option separator unchanged."""
    arguments = ["--", "--db_password", "secret"]
    sanitized, environment = sanitize_odoo_password(arguments, {})
    assert sanitized == arguments
    assert "PGPASSWORD" not in environment


def test_data_dir_precedence_matches_other_command_options(tmp_path: Path):
    """Explicit command input overrides argv, which overrides Odoo config."""
    cfg = config(tmp_path)
    configured = tmp_path / "configured"
    forwarded = tmp_path / "forwarded"
    direct = tmp_path / "direct"
    cfg.odoo_conf_path.write_text(f"[options]\ndata_dir = {configured}\n")
    resolved = resolve_odoo_config(cfg, ["-D" + str(forwarded)])
    assert resolved.data_dir == forwarded
    resolved = resolve_odoo_config(cfg, ["-D" + str(forwarded)], direct={"data_dir": direct})
    assert resolved.data_dir == direct


def test_attached_short_options_are_resolved_before_preflight(tmp_path: Path):
    """Attached Odoo short options contribute to the effective configuration."""
    cfg = config(tmp_path)
    selected_data_dir = tmp_path / "selected-data"
    resolved = resolve_odoo_config(cfg, ["-wselected-password", "-D" + str(selected_data_dir)])
    assert resolved.db_password == "selected-password"
    assert resolved.data_dir == selected_data_dir


def test_launch_normalizes_legacy_option_chunks_before_resolution(tmp_path: Path):
    """Preflight and launch consume the same normalized selected config option."""
    from godoo_cli.commands.runtime.launch import launch_odoo

    cfg = config(tmp_path)
    paths: OdooCommandPaths = {
        "odoo_main_path": cfg.odoo_install_folder,
        "workspace_addon_path": cfg.workspace_addon_path,
        "odoo_conf_path": cfg.odoo_conf_path,
    }
    selected = tmp_path / "selected.conf"
    with (
        patch("godoo_cli.commands.runtime.launch.require_cli_odoo_version"),
        patch("godoo_cli.commands.runtime.launch.preflight_for_config") as preflight,
        patch("godoo_cli.commands.runtime.launch.build_launch_command", return_value=["odoo-bin"]) as build,
        patch("godoo_cli.commands.runtime.launch.run_odoo_command", return_value=SimpleNamespace(returncode=0)),
    ):
        assert launch_odoo(extra_args=[f"--config {selected}"], **paths) == 0

    assert preflight.call_args.args[0].odoo_conf_path == selected
    assert preflight.call_args.args[1] == ["--config", str(selected)]
    assert build.call_args.args[0].odoo_conf_path == selected


def test_run_forwards_arbitrary_arguments_and_help_bypasses_preflight(tmp_path: Path):
    """Guards the contract that run forwards arbitrary arguments and help bypasses preflight."""
    cfg = config(tmp_path)
    paths: OdooCommandPaths = {
        "odoo_main_path": cfg.odoo_install_folder,
        "workspace_addon_path": cfg.workspace_addon_path,
        "odoo_conf_path": cfg.odoo_conf_path,
    }
    arguments = ["--help", "-c", "selected.conf", "-d", "a,b"]
    with (
        patch("godoo_cli.commands.run.preflight_for_config") as preflight,
        patch("godoo_cli.commands.run.run_odoo_command", return_value=SimpleNamespace(returncode=0)) as run,
    ):
        assert run_odoo(arguments, **paths) == 0
    preflight.assert_called_once()
    assert preflight.call_args.args[1] == arguments
    child = run.call_args.args[0]
    assert child[1 : 1 + len(arguments)] == arguments
    assert child[child.index("--config") + 1] == str(Path("selected.conf").absolute())


def test_preflight_rejects_old_database_before_venv_or_dependency_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    object.__setattr__(cfg, "db_connection", FakeConnection())
    callbacks: list[str] = []
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.require_supported_odoo_runtime",
        lambda _path: OdooVersion(text="Odoo", major=19, minor=0),
    )
    monkeypatch.setattr("godoo_cli.runtime.odoo.require_runtime_database_major", require_runtime_database_major)
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.classify_bootstrap_state",
        lambda _connection: DbBootstrapStatus.BOOTSTRAPPED,
    )
    monkeypatch.setattr("godoo_cli.runtime.odoo.base_module_major", lambda _connection: 18)
    monkeypatch.setattr("godoo_cli.runtime.odoo.subprocess.run", lambda *_args, **_kwargs: callbacks.append("venv"))
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.dependency_requirements",
        lambda *_args, **_kwargs: callbacks.append("dependencies"),
    )

    with pytest.raises(RuntimeError, match=r"contains Odoo 18.*godoo db load"):
        preflight_for_config(cfg, project_root=tmp_path)

    assert callbacks == []


@pytest.mark.parametrize("status", [DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB])
def test_runtime_database_major_allows_missing_or_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: DbBootstrapStatus
) -> None:
    cfg = config(tmp_path)
    object.__setattr__(cfg, "db_connection", FakeConnection())
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.require_supported_odoo_runtime",
        lambda _path: OdooVersion(text="Odoo", major=19, minor=0),
    )
    monkeypatch.setattr("godoo_cli.runtime.odoo.classify_bootstrap_state", lambda _connection: status)
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.base_module_major",
        lambda _connection: pytest.fail("empty database must not query the base version"),
    )

    assert require_runtime_database_major(cfg) == 19


@pytest.mark.parametrize("status", [DbBootstrapStatus.INVALID_DB, DbBootstrapStatus.BOOTSTRAPPED])
def test_runtime_database_major_rejects_unusable_base_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: DbBootstrapStatus
) -> None:
    cfg = config(tmp_path)
    object.__setattr__(cfg, "db_connection", FakeConnection())
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.require_supported_odoo_runtime",
        lambda _path: OdooVersion(text="Odoo", major=19, minor=0),
    )
    monkeypatch.setattr("godoo_cli.runtime.odoo.classify_bootstrap_state", lambda _connection: status)

    def unreadable_base_version(_connection: object) -> int:
        message = "no usable base version"
        raise RuntimeError(message)

    monkeypatch.setattr("godoo_cli.runtime.odoo.base_module_major", unreadable_base_version)

    with pytest.raises(RuntimeError, match=r"Database 'db-a'.*godoo db load.*service result"):
        require_runtime_database_major(cfg)


def test_runtime_database_major_checks_each_explicit_database_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    object.__setattr__(cfg, "db_name", "db-a,db-b,db-a")
    connections = {name: FakeConnection() for name in ("db-a", "db-b")}
    for name, connection in connections.items():
        connection.names = [name]
    object.__setattr__(cfg, "db_connection", MultiDatabaseConnection(connections))
    observed: list[str] = []
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.require_supported_odoo_runtime",
        lambda _path: OdooVersion(text="Odoo", major=19, minor=0),
    )
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.classify_bootstrap_state",
        lambda connection: observed.append(connection.names[0]) or DbBootstrapStatus.BOOTSTRAPPED,
    )
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.base_module_major",
        lambda connection: observed.append(connection.names[0]) or 19,
    )

    assert require_runtime_database_major(cfg) == 19
    assert observed == ["db-a", "db-a", "db-b", "db-b"]


@pytest.mark.parametrize("marker_kind", ["restore", "lifecycle"])
def test_launch_readiness_rejects_pending_recovery_markers(tmp_path: Path, marker_kind: str) -> None:
    cfg = config(tmp_path)
    data_dir = tmp_path / "data"
    object.__setattr__(cfg, "data_dir", data_dir)
    if marker_kind == "restore":
        marker = runtime_restore_marker(data_dir, cfg.db_name)
    else:
        marker = runtime_readiness_marker(data_dir, cfg.db_name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker_kind == "lifecycle":
        marker.write_text(
            '{"schema_version":2,"database":"db-a","outcome":"unknown",'
            '"pending_phase":"initialize","plan":{"identity":{},"diagnostics":{}}}',
            encoding="utf-8",
        )
    else:
        marker.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"pending restore|pending lifecycle|unfinished"):
        require_runtime_launch_ready(cfg)


@pytest.mark.parametrize("marker_kind", ["restore", "lifecycle"])
def test_managed_run_server_rejects_pending_recovery_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker_kind: str
) -> None:
    cfg = config(tmp_path)
    data_dir = tmp_path / "data"
    object.__setattr__(cfg, "data_dir", data_dir)
    marker = (
        runtime_restore_marker(data_dir, cfg.db_name)
        if marker_kind == "restore"
        else runtime_readiness_marker(data_dir, cfg.db_name)
    )
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker_kind == "lifecycle":
        marker.write_text(
            '{"schema_version":2,"database":"db-a","outcome":"unknown",'
            '"pending_phase":"initialize","plan":{"identity":{},"diagnostics":{}}}',
            encoding="utf-8",
        )
    else:
        marker.write_text("{}", encoding="utf-8")
    paths: OdooCommandPaths = {
        "odoo_main_path": cfg.odoo_install_folder,
        "workspace_addon_path": cfg.workspace_addon_path,
        "odoo_conf_path": cfg.odoo_conf_path,
    }
    monkeypatch.setattr("godoo_cli.commands.run.resolve_command_config", lambda **_kwargs: cfg)
    callbacks: list[str] = []
    monkeypatch.setattr(
        "godoo_cli.commands.run.preflight_for_config", lambda *_args, **_kwargs: callbacks.append("preflight")
    )
    monkeypatch.setattr("godoo_cli.commands.run.run_odoo_command", lambda *_args, **_kwargs: callbacks.append("run"))

    with pytest.raises(RuntimeError, match=r"pending restore|pending lifecycle|unfinished"):
        run_odoo(["server"], data_dir=data_dir, db_name=cfg.db_name, **paths)

    assert callbacks == []


def test_diagnostic_shell_allows_matching_database_during_pending_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    data_dir = tmp_path / "data"
    object.__setattr__(cfg, "data_dir", data_dir)
    marker = runtime_readiness_marker(data_dir, cfg.db_name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}", encoding="utf-8")
    object.__setattr__(cfg, "db_connection", FakeConnection())
    monkeypatch.setattr("godoo_cli.commands.run.resolve_command_config", lambda **_kwargs: cfg)
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.require_supported_odoo_runtime",
        lambda _path: OdooVersion(text="Odoo", major=19, minor=0),
    )
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.classify_bootstrap_state",
        lambda _connection: DbBootstrapStatus.BOOTSTRAPPED,
    )
    monkeypatch.setattr("godoo_cli.runtime.odoo.base_module_major", lambda _connection: 19)
    monkeypatch.setattr("godoo_cli.commands.run.preflight_for_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "godoo_cli.commands.run.run_odoo_command", lambda *_args, **_kwargs: SimpleNamespace(returncode=0)
    )
    paths: OdooCommandPaths = {
        "odoo_main_path": cfg.odoo_install_folder,
        "workspace_addon_path": cfg.workspace_addon_path,
        "odoo_conf_path": cfg.odoo_conf_path,
    }

    assert run_odoo(["shell"], data_dir=data_dir, db_name=cfg.db_name, **paths) == 0
