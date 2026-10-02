"""Tests lifecycle orchestration across runtime setup and deployment initialization."""

import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.commands.runtime import init as lifecycle_commands
from godoo_cli.database.connection import DBConnection
from godoo_cli.database.state import DbBootstrapStatus
from godoo_cli.models import GodooConfig
from godoo_cli.runtime import lifecycle as runtime_lifecycle
from godoo_cli.runtime.lifecycle import (
    LifecycleBootstrapError,
    LifecycleOutcome,
    ensure_runtime,
)

LOGGER = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def supported_odoo_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lifecycle_commands,
        "require_odoo_version",
        lambda *_args: SimpleNamespace(major=19, raw="19.0"),
    )
    monkeypatch.setattr(runtime_lifecycle, "require_runtime_database_major", lambda _config: 19)


def _config(tmp_path: Path) -> GodooConfig:
    return GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        db_name="runtime",
        data_dir=tmp_path / "data",
    )


def test_ensure_runtime_prepares_and_skips_existing_database(tmp_path: Path):
    """Guards the contract that ensure runtime prepares and skips existing database."""
    calls: list[str] = []

    created = ensure_runtime(
        _config(tmp_path),
        preparer=lambda _config: calls.append("prepare"),
        status_getter=lambda _connection: DbBootstrapStatus.BOOTSTRAPPED,
        bootstrapper=lambda *_args, **_kwargs: calls.append("bootstrap") or 0,
    )

    assert created is False
    assert calls == ["prepare"]


def test_ensure_runtime_prepares_and_bootstraps_missing_database(tmp_path: Path):
    """Prepare runtime configuration before bootstrapping a missing database."""
    calls: list[str] = []
    bootstrap_args: list[str] = []

    def bootstrapper(*_args: object, **kwargs: object) -> int:
        bootstrap_args.extend(cast(list[str], kwargs["extra_cmd_args"]))
        calls.append("bootstrap")
        return 0

    created = ensure_runtime(
        _config(tmp_path),
        preparer=lambda _config: calls.append("prepare"),
        status_getter=lambda _connection: DbBootstrapStatus.NO_DB,
        bootstrapper=bootstrapper,
    )

    assert created is True
    assert calls == ["prepare", "bootstrap"]
    assert bootstrap_args == ["--without-demo"]
    assert "all" not in bootstrap_args


def test_ensure_runtime_reports_native_bootstrap_failure(tmp_path: Path):
    """Guards the contract that ensure runtime reports native bootstrap failure."""
    with pytest.raises(LifecycleBootstrapError) as error:
        ensure_runtime(
            _config(tmp_path),
            preparer=lambda _config: None,
            status_getter=lambda _connection: DbBootstrapStatus.EMPTY_DB,
            bootstrapper=lambda *_args, **_kwargs: 17,
        )

    assert error.value.return_code == 17


def test_reconcile_modules_passes_addon_paths_when_config_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that reconcile modules passes addon paths when config is missing."""
    config = _config(tmp_path)
    (config.workspace_addon_path / "sale").mkdir(parents=True)
    (config.workspace_addon_path / "sale" / "__manifest__.py").write_text("{}")
    observed: dict[str, object] = {}

    def run_command(command: list[str]) -> SimpleNamespace:
        observed["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runtime_lifecycle, "run_odoo_command", run_command)
    assert runtime_lifecycle.reconcile_modules(config, ["sale"], None) == 0

    command = cast(list[str], observed["command"])
    assert command[command.index("--addons-path") + 1] == str(config.workspace_addon_path.absolute())


def test_reconcile_modules_builds_typed_upgrade_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that reconcile modules builds typed upgrade arguments."""
    config = _config(tmp_path)
    config.odoo_conf_path.write_text("[options]\n")
    observed: dict[str, list[str]] = {}
    monkeypatch.setattr(
        runtime_lifecycle,
        "run_odoo_command",
        lambda command: observed.update(command=command) or SimpleNamespace(returncode=0),
    )

    result = runtime_lifecycle.reconcile_modules(
        config,
        ["sale,stock", "sale"],
        ["web"],
        upgrade_path=[tmp_path / "upgrades", tmp_path / "extra-upgrades"],
        pre_upgrade_scripts=[tmp_path / "first.py", tmp_path / "second.py"],
        log_handlers=["odoo.modules:DEBUG,odoo.sql_db:INFO", "odoo.modules:DEBUG"],
    )

    assert result == 0
    command = observed["command"]
    assert command[-1] == "--no-http"
    assert "--save" not in command

    def option_values(option: str) -> list[str]:
        return [command[index + 1] for index, value in enumerate(command[:-1]) if value == option]

    assert option_values("--database") == ["runtime"]
    assert option_values("--db_user") == [""]
    assert option_values("--db_host") == [""]
    assert option_values("--db_port") == ["0"]
    assert option_values("--update") == ["sale,stock"]
    assert option_values("--init") == ["web"]
    assert option_values("--upgrade-path") == [f"{tmp_path / 'upgrades'},{tmp_path / 'extra-upgrades'}"]
    assert option_values("--pre-upgrade-scripts") == [f"{tmp_path / 'first.py'},{tmp_path / 'second.py'}"]
    assert option_values("--log-handler") == ["odoo.modules:DEBUG", "odoo.sql_db:INFO"]


def test_upgrade_options_require_an_explicit_update(tmp_path: Path):
    """Guards the contract that upgrade options require an explicit update."""
    with pytest.raises(ValueError, match="require at least one --update"):
        runtime_lifecycle.reconcile_modules(
            _config(tmp_path),
            None,
            ["web"],
            upgrade_path=tmp_path / "upgrades",
        )


def test_reconcile_preflight_includes_selected_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that reconcile preflight includes selected modules."""
    observed: list[str] = []
    monkeypatch.setattr(
        runtime_lifecycle,
        "preflight_for_config",
        lambda _config, arguments, **_kwargs: observed.extend(arguments),
    )

    runtime_lifecycle.preflight_reconcile_dependencies(
        _config(tmp_path),
        ["sale,stock", "sale"],
        ["web"],
    )

    assert observed == ["--update", "sale,stock", "--init", "web"]


def test_reconcile_preflight_allows_missing_installed_modules_for_pre_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Pass the pre-upgrade reconciliation allowance to dependency preflight."""
    observed: dict[str, object] = {}

    def preflight(_config: GodooConfig, _arguments: list[str], **kwargs: object) -> None:
        observed.update(kwargs)

    monkeypatch.setattr(runtime_lifecycle, "preflight_for_config", preflight)

    runtime_lifecycle.preflight_reconcile_dependencies(
        _config(tmp_path),
        ["base"],
        None,
        ignore_missing_installed_modules=True,
    )

    assert observed == {"ignore_missing_installed_modules": True}
    return
    app = typer.Typer()

    def reconcile(_config: GodooConfig, **kwargs: object) -> int:
        observed.update(kwargs)
        assert "dependency_resolver" not in kwargs
        return 0

    def init(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        observed["init_kwargs"] = kwargs
        reconciler = cast(Callable[[GodooConfig], int], kwargs["reconciler"])
        assert callable(reconciler)
        return LifecycleOutcome.READY, reconciler(config)

    monkeypatch.setattr(lifecycle_commands, "deployment_init", init)
    monkeypatch.setattr(lifecycle_commands, "reconcile_runtime", reconcile)
    result = CliRunner().invoke(
        app,
        [],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
            "GODOO_RECONCILE_DEPENDENCIES": "true",
        },
    )

    assert result.exit_code == 0, result.output
    assert cast(dict[str, object], observed["init_kwargs"])["after_reconcile_dirs"] is None


def test_deployment_init_passes_fresh_bootstrap_policy(monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that deployment init passes fresh bootstrap policy."""
    observed: dict[str, object] = {}
    app = typer.Typer()
    app.command()(lifecycle_commands.deployment_init_odoo_runtime)

    def ensure(_config: GodooConfig, **kwargs: object) -> bool:
        observed.update(kwargs)
        return True

    def init(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        ensure_runtime = cast(Callable[[GodooConfig], bool], kwargs["ensure"])
        assert ensure_runtime(config) is True
        return LifecycleOutcome.BOOTSTRAPPED, 0

    monkeypatch.setattr(lifecycle_commands, "ensure_runtime", ensure)
    monkeypatch.setattr(lifecycle_commands, "deployment_init", init)
    result = CliRunner().invoke(
        app,
        ["--no-install-workspace-modules", "--extra-bootstrap-args=--load-language=de_DE"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
            "GODOO_RUNTIME_DEMO": "true",
        },
    )

    assert result.exit_code == 0, result.output
    assert observed["odoo_demo"] is True
    assert observed["install_workspace_modules"] is False
    assert observed["extra_bootstrap_args"] == ["--load-language=de_DE"]


def test_deployment_init_preflights_pre_upgrade_before_and_after_restore(
    monkeypatch: pytest.MonkeyPatch,
):
    """Guards the contract that deployment init preflights requested modules before and after restore."""
    observed: list[tuple[list[str], dict[str, object]]] = []
    app = typer.Typer()
    app.command()(lifecycle_commands.deployment_init_odoo_runtime)

    def init(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        preflight = cast(Callable[[GodooConfig], None], kwargs["preflight"])
        post_restore_preflight = cast(Callable[[GodooConfig], None], kwargs["post_restore_preflight"])
        preflight(config)
        post_restore_preflight(config)
        return LifecycleOutcome.RESTORED, 0

    monkeypatch.setattr(lifecycle_commands, "deployment_init", init)
    monkeypatch.setattr(
        runtime_lifecycle,
        "preflight_for_config",
        lambda _config, arguments, **kwargs: observed.append((arguments, kwargs)),
    )

    result = CliRunner().invoke(
        app,
        ["--update", "base", "--pre-upgrade-script", "/tmp/pre-upgrade.py"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )

    assert result.exit_code == 0, result.output
    assert observed == [
        (["--update", "base"], {"ignore_missing_installed_modules": True}),
        (["--update", "base"], {"ignore_missing_installed_modules": True}),
    ]


def test_deployment_init_loads_native_seed_archive_through_odoo(
    monkeypatch: pytest.MonkeyPatch,
):
    """Guards the contract that deployment init loads native seed archive through odoo."""
    observed: dict[str, object] = {}
    app = typer.Typer()
    app.command()(lifecycle_commands.deployment_init_odoo_runtime)

    def load_archive(**kwargs: object) -> int:
        observed.update(kwargs)
        return 0

    def init(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        assert kwargs["seed_requested"] is True
        seeder = cast(Callable[[GodooConfig], None], kwargs["seeder"])
        seeder(config)
        return LifecycleOutcome.RESTORED, 0

    monkeypatch.setattr(lifecycle_commands, "load_runtime_archive", load_archive)
    monkeypatch.setattr(lifecycle_commands, "deployment_init", init)

    result = CliRunner().invoke(
        app,
        ["--seed", "/tmp/runtime.zip"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )

    assert result.exit_code == 0, result.output
    assert observed["archive_path"] == Path("/tmp/runtime.zip")
    assert observed["db_name"] == "runtime"
    assert observed["data_dir"] == Path("/var/lib/odoo")
    assert observed["force"] is True
    assert observed["use_native_db_load"] is None
    connection = observed["connection"]
    assert isinstance(connection, DBConnection)
    assert connection.db_name == "runtime"
    assert connection.username == "odoo"


def test_deployment_init_uses_sql_seed_load_before_pre_upgrade_scripts(
    monkeypatch: pytest.MonkeyPatch,
):
    """Restore through PostgreSQL before Odoo can run pre-upgrade scripts."""
    observed: dict[str, object] = {}
    app = typer.Typer()
    app.command()(lifecycle_commands.deployment_init_odoo_runtime)

    def load_archive(**kwargs: object) -> int:
        observed.update(kwargs)
        return 0

    def init(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        seeder = cast(Callable[[GodooConfig], None], kwargs["seeder"])
        seeder(config)
        return LifecycleOutcome.RESTORED, 0

    monkeypatch.setattr(lifecycle_commands, "load_runtime_archive", load_archive)
    monkeypatch.setattr(lifecycle_commands, "deployment_init", init)

    result = CliRunner().invoke(
        app,
        ["--seed", "/tmp/runtime.zip", "--update", "base", "--pre-upgrade-script", "/tmp/pre-upgrade.py"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )

    assert result.exit_code == 0, result.output
    assert observed["use_native_db_load"] is False


def test_deployment_init_rejects_after_restore_hooks_with_pre_upgrade_scripts():
    """Prevent restore hooks from creating Odoo's registry before reconciliation."""
    app = typer.Typer()
    app.command()(lifecycle_commands.deployment_init_odoo_runtime)

    result = CliRunner().invoke(
        app,
        [
            "--seed",
            "/tmp/runtime.zip",
            "--pre-upgrade-script",
            "/tmp/pre-upgrade.py",
            "--after-restore-dir",
            "/tmp/hooks",
        ],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )

    assert result.exit_code == 2
    assert "cannot run with" in result.output


def test_deployment_init_allows_after_restore_hooks_without_seed_for_pre_upgrade(
    monkeypatch: pytest.MonkeyPatch,
):
    """Allow ready-runtime reconciliation to retain its ordinary hook configuration."""
    app = typer.Typer()
    app.command()(lifecycle_commands.deployment_init_odoo_runtime)
    monkeypatch.setattr(
        lifecycle_commands,
        "deployment_init",
        lambda *_args, **_kwargs: (LifecycleOutcome.READY, 0),
    )

    result = CliRunner().invoke(
        app,
        ["--update", "base", "--pre-upgrade-script", "/tmp/pre-upgrade.py", "--after-restore-dir", "/tmp/hooks"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )

    assert result.exit_code == 0, result.output


def test_deployment_init_rejects_pre_upgrade_scripts_without_update():
    """Fail before seed restoration when Odoo has no module update to run."""
    app = typer.Typer()
    app.command()(lifecycle_commands.deployment_init_odoo_runtime)

    result = CliRunner().invoke(
        app,
        ["--seed", "/tmp/runtime.zip", "--pre-upgrade-script", "/tmp/pre-upgrade.py"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )

    assert result.exit_code == 2
    assert "requires at least one" in result.output


def test_expected_odoo_major_rejects_before_deployment_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject a transition target that disagrees with the configured Odoo runtime."""
    app = typer.Typer()
    app.command()(lifecycle_commands.deployment_init_odoo_runtime)
    monkeypatch.setattr(
        lifecycle_commands,
        "deployment_init",
        lambda *_args, **_kwargs: pytest.fail("version mismatch must reject before deployment"),
    )
    result = CliRunner().invoke(
        app,
        ["--expected-odoo-major", "18"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )
    assert result.exit_code == 2
    assert "requires Odoo 18.x" in result.output


def test_reconcile_modules_keeps_single_path_api_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep existing direct callers that pass one Path working."""
    observed: dict[str, list[str]] = {}
    monkeypatch.setattr(
        runtime_lifecycle,
        "run_odoo_command",
        lambda command: observed.update(command=command) or SimpleNamespace(returncode=0),
    )
    upgrade_root = tmp_path / "upgrades"
    assert (
        runtime_lifecycle.reconcile_modules(
            _config(tmp_path),
            ["sale"],
            None,
            upgrade_path=upgrade_root,
        )
        == 0
    )
    command = observed["command"]
    assert command[command.index("--upgrade-path") + 1] == str(upgrade_root.resolve())
