from pathlib import Path
from types import SimpleNamespace
from typing import Callable, cast

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.commands import lifecycle as lifecycle_commands
from godoo_cli.commands.db.query import DbBootstrapStatus
from godoo_cli.lifecycle import (
    LifecycleBootstrapError,
    LifecycleOutcome,
    ensure_runtime,
)
from godoo_cli.models import GodooConfig


def _config(tmp_path: Path) -> GodooConfig:
    return GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        db_name="runtime",
    )


def test_ensure_runtime_prepares_and_skips_existing_database(tmp_path: Path):
    calls: list[str] = []

    created = ensure_runtime(
        _config(tmp_path),
        preparer=lambda _config: calls.append("prepare"),
        status_getter=lambda _connection: DbBootstrapStatus.BOOTSTRAPPED,
        bootstrapper=lambda *_args, **_kwargs: calls.append("bootstrap") or 0,
    )

    assert created is False
    assert calls == ["prepare"]


def test_ensure_runtime_syncs_and_bootstraps_missing_database(tmp_path: Path):
    calls: list[str] = []

    created = ensure_runtime(
        _config(tmp_path),
        source_synchronizer=lambda: calls.append("sync"),
        preparer=lambda _config: calls.append("prepare"),
        status_getter=lambda _connection: DbBootstrapStatus.NO_DB,
        bootstrapper=lambda *_args, **_kwargs: calls.append("bootstrap") or 0,
    )

    assert created is True
    assert calls == ["sync", "prepare", "bootstrap"]


def test_ensure_runtime_reports_native_bootstrap_failure(tmp_path: Path):
    with pytest.raises(LifecycleBootstrapError) as error:
        ensure_runtime(
            _config(tmp_path),
            preparer=lambda _config: None,
            status_getter=lambda _connection: DbBootstrapStatus.EMPTY_DB,
            bootstrapper=lambda *_args, **_kwargs: 17,
        )

    assert error.value.return_code == 17


def test_reconcile_cli_uses_environment_defaults(monkeypatch: pytest.MonkeyPatch):
    observed: dict[str, object] = {}
    app = typer.Typer()
    app.command()(lifecycle_commands.reconcile_odoo_runtime)

    def reconcile(_config: GodooConfig, **kwargs: object) -> int:
        observed.update(kwargs)
        return 0

    monkeypatch.setattr(lifecycle_commands, "reconcile_runtime", reconcile)
    monkeypatch.setattr(
        lifecycle_commands,
        "_reconcile_modules",
        lambda _config, update, install, **_kwargs: observed.update(update=update, install=install) or 0,
    )
    result = CliRunner().invoke(
        app,
        [],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_THIRDPARTY_LOCATION": "/tmp/thirdparty",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
            "GODOO_RECONCILE_UPDATE": "sale",
            "GODOO_RECONCILE_INSTALL": "stock",
        },
    )
    assert result.exit_code == 0, result.output
    reconciler = observed["reconciler"]
    assert callable(reconciler)
    assert reconciler(_config(Path("/tmp"))) == 0
    assert (observed["update"], observed["install"]) == (["sale"], ["stock"])


def test_reconcile_modules_passes_addon_paths_when_config_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = _config(tmp_path)
    (config.workspace_addon_path / "sale").mkdir(parents=True)
    (config.workspace_addon_path / "sale" / "__manifest__.py").write_text("{}")
    observed: dict[str, object] = {}

    def run_command(command: list[str]) -> SimpleNamespace:
        observed["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lifecycle_commands, "run_odoo_command", run_command)
    assert lifecycle_commands._reconcile_modules(config, ["sale"], None) == 0

    command = cast(list[str], observed["command"])
    assert command[command.index("--addons-path") + 1] == str(config.workspace_addon_path.absolute())


def test_reconcile_modules_builds_typed_upgrade_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = _config(tmp_path)
    config.odoo_conf_path.write_text("[options]\n")
    observed: dict[str, list[str]] = {}
    monkeypatch.setattr(
        lifecycle_commands,
        "run_odoo_command",
        lambda command: observed.update(command=command) or SimpleNamespace(returncode=0),
    )

    result = lifecycle_commands._reconcile_modules(
        config,
        ["sale,stock", "sale"],
        ["web"],
        upgrade_path=tmp_path / "upgrades",
        pre_upgrade_scripts=[tmp_path / "first.py", tmp_path / "second.py"],
        log_handlers=["odoo.modules:DEBUG,odoo.sql_db:INFO", "odoo.modules:DEBUG"],
    )

    assert result == 0
    assert observed["command"] == [
        str((config.odoo_install_folder / "odoo-bin").absolute()),
        "--config",
        str(config.odoo_conf_path.absolute()),
        "--data-dir",
        str(config.data_dir.absolute()),
        "--stop-after-init",
        "--update",
        "sale,stock",
        "--init",
        "web",
        "--upgrade-path",
        str(tmp_path / "upgrades"),
        "--pre-upgrade-scripts",
        f"{tmp_path / 'first.py'},{tmp_path / 'second.py'}",
        "--log-handler",
        "odoo.modules:DEBUG",
        "--log-handler",
        "odoo.sql_db:INFO",
    ]


def test_upgrade_options_require_an_explicit_update(tmp_path: Path):
    with pytest.raises(ValueError, match="require at least one --update"):
        lifecycle_commands._reconcile_modules(
            _config(tmp_path),
            None,
            ["web"],
            upgrade_path=tmp_path / "upgrades",
        )


def test_deployment_init_resolves_installed_dependencies_when_requested(monkeypatch: pytest.MonkeyPatch):
    observed: dict[str, object] = {}
    app = typer.Typer()
    app.command()(lifecycle_commands.deployment_init_odoo_runtime)

    def reconcile(config: GodooConfig, **kwargs: object) -> int:
        observed.update(kwargs)
        resolver = cast(Callable[[GodooConfig], int], kwargs["dependency_resolver"])
        assert callable(resolver)
        return resolver(config)

    def init(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        observed["init_kwargs"] = kwargs
        reconciler = cast(Callable[[GodooConfig], int], kwargs["reconciler"])
        assert callable(reconciler)
        return LifecycleOutcome.READY, reconciler(config)

    monkeypatch.setattr(lifecycle_commands, "deployment_init", init)
    monkeypatch.setattr(lifecycle_commands, "reconcile_runtime", reconcile)
    monkeypatch.setattr(
        lifecycle_commands,
        "_resolve_installed_dependencies",
        lambda _config: observed.update(dependencies_resolved=True) or 0,
    )
    result = CliRunner().invoke(
        app,
        ["--pre-launch-hooks-dir", "/tmp/hooks"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_THIRDPARTY_LOCATION": "/tmp/thirdparty",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
            "GODOO_RECONCILE_DEPENDENCIES": "true",
        },
    )

    assert result.exit_code == 0, result.output
    assert observed["dependencies_resolved"] is True
    assert cast(dict[str, object], observed["init_kwargs"])["after_reconcile_dirs"] == [Path("/tmp/hooks")]


def test_deployment_init_passes_fresh_bootstrap_policy(monkeypatch: pytest.MonkeyPatch):
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
            "ODOO_THIRDPARTY_LOCATION": "/tmp/thirdparty",
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


def test_deployment_init_loads_native_seed_archive_through_odoo(
    monkeypatch: pytest.MonkeyPatch,
):
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
            "ODOO_THIRDPARTY_LOCATION": "/tmp/thirdparty",
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


def test_deployment_init_loads_legacy_seed_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    seed = tmp_path / "legacy"
    seed.mkdir()
    observed: dict[str, object] = {}
    app = typer.Typer()
    app.command()(lifecycle_commands.deployment_init_odoo_runtime)

    monkeypatch.setattr(lifecycle_commands, "load_legacy_runtime_dump", lambda **kwargs: observed.update(kwargs))

    def init(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        cast(Callable[[GodooConfig], None], kwargs["seeder"])(config)
        return LifecycleOutcome.RESTORED, 0

    monkeypatch.setattr(lifecycle_commands, "deployment_init", init)
    result = CliRunner().invoke(
        app,
        ["--seed", str(seed)],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_THIRDPARTY_LOCATION": "/tmp/thirdparty",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )

    assert result.exit_code == 0, result.output
    assert observed["source_folder"] == seed
    assert observed["db_template"] == "template0"


@pytest.mark.parametrize(
    ("stage_value", "password_value", "expected"),
    [
        ("false", "false", (False, False)),
        ("0", "0", (False, False)),
        ("true", "1", (True, True)),
    ],
)
def test_dev_parses_post_bootstrap_environment_flags(
    monkeypatch: pytest.MonkeyPatch,
    stage_value: str,
    password_value: str,
    expected: tuple[bool, bool],
):
    observed: dict[str, bool] = {}
    app = typer.Typer()
    app.command()(lifecycle_commands.dev_odoo)
    monkeypatch.setattr(lifecycle_commands, "_ensure_config", lambda *_args, **_kwargs: True)

    def run_hooks(
        _config: GodooConfig,
        *,
        staging: bool,
        set_dev_password: bool,
        migrations_dir: Path,
    ) -> int:
        del migrations_dir
        observed["staging"] = staging
        observed["set_dev_password"] = set_dev_password
        return 0

    monkeypatch.setattr(lifecycle_commands, "_run_devcontainer_hooks", run_hooks)
    result = CliRunner().invoke(
        app,
        ["--no-launch"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_THIRDPARTY_LOCATION": "/tmp/thirdparty",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
            "GODOO_LAUNCH_STAGE": stage_value,
            "GODOO_DEV_SET_PW": password_value,
        },
    )

    assert result.exit_code == 0, result.output
    assert (observed["staging"], observed["set_dev_password"]) == expected
