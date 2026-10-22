"""Migration selection feeds the existing init lifecycle without runtime side effects."""

import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import typer
from click import unstyle
from typer.testing import CliRunner

from godoo_cli.commands.runtime import init as runtime_init
from godoo_cli.models import GodooConfig
from godoo_cli.runtime.lifecycle import LifecycleOutcome
from godoo_cli.runtime.migration import resolve_migration_directory

CONFLICTING_ENVIRONMENT = (
    "GODOO_RECONCILE_UPDATE",
    "GODOO_RECONCILE_INSTALL",
    "GODOO_RECONCILE_PRE_UPGRADE_SCRIPTS",
    "GODOO_RECONCILE_UPGRADE_PATH",
    "GODOO_AFTER_BOOTSTRAP_DIRS",
    "GODOO_AFTER_RESTORE_DIRS",
    "GODOO_AFTER_RECONCILE_DIRS",
)


@pytest.fixture
def migration_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, str]]:
    """Create portable migration inputs and isolate CLI defaults."""
    monkeypatch.chdir(tmp_path)
    for variable in (
        *CONFLICTING_ENVIRONMENT,
        "GODOO_EXPECTED_ODOO_MAJOR",
        "GODOO_MIGRATION_DIR",
        "GODOO_RUNTIME_DEMO",
    ):
        monkeypatch.delenv(variable, raising=False)
    directory = tmp_path / "transitions" / "16-19"
    (directory / "after-reconcile").mkdir(parents=True)
    (directory / "after-reconcile" / "01-finish.py").write_text("pass\n")
    (directory / "pre-upgrade.py").write_text("pass\n")
    (directory / "upgrades").mkdir()
    (tmp_path / "migration" / "upgrade").mkdir(parents=True)
    archive = tmp_path / "seed.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("dump.sql", "-- test seed\n")
    filestore = tmp_path / "original"
    filestore.mkdir()
    (filestore / "attachment").write_text("attachment")
    config = GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        data_dir=tmp_path / "data",
        db_name="runtime",
    )
    monkeypatch.setattr(runtime_init, "resolve_command_config", lambda **_kwargs: config)
    monkeypatch.setattr(runtime_init, "require_odoo_version", lambda *_args: SimpleNamespace(major=19, raw="19.0"))
    monkeypatch.setattr(runtime_init, "x_sendfile_enabled", lambda *_args: False)
    environment = {
        "ODOO_MAIN_FOLDER": str(config.odoo_install_folder),
        "ODOO_WORKSPACE_ADDON_LOCATION": str(config.workspace_addon_path),
        "ODOO_CONF_PATH": str(config.odoo_conf_path),
        "ODOO_DB_FILTER": ".*",
        "ODOO_MAIN_DB": "runtime",
        "ODOO_DB_USER": "odoo",
        "GODOO_RUNTIME_SEED": str(archive),
        "GODOO_ORIGINAL_FILESTORE": str(filestore),
    }
    return directory, environment


def _app() -> typer.Typer:
    """Expose init without loading unrelated project defaults."""
    app = typer.Typer()
    app.command()(runtime_init.deployment_init_odoo_runtime)
    return app


def test_dump_path_routes_plain_dataset_to_custom_restore(
    migration_environment: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, environment = migration_environment
    dataset = workspace / "dataset"
    filestore = dataset / "odoo_filestore" / "filestore" / "odoo"
    filestore.mkdir(parents=True)
    dump = dataset / "odoo.dump"
    dump.write_bytes(b"custom dump")
    environment.pop("GODOO_RUNTIME_SEED")
    environment.pop("GODOO_ORIGINAL_FILESTORE")
    environment["GODOO_DUMP_PATH"] = str(dataset)
    monkeypatch.setattr("godoo_cli.runtime.dataset.validate_custom_dump", lambda _path: None)
    restored: dict[str, object] = {}
    observed: dict[str, object] = {}

    def restore(**kwargs: object) -> None:
        restored.update(kwargs)

    def deployment(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        observed.update(kwargs)
        cast(Callable[[GodooConfig], None], kwargs["seeder"])(config)
        return LifecycleOutcome.RESTORED, 0

    monkeypatch.setattr(runtime_init, "restore_custom_runtime", restore)
    monkeypatch.setattr(runtime_init, "deployment_init", deployment)
    result = CliRunner().invoke(_app(), [], env=environment)

    assert result.exit_code == 0, result.output
    assert restored["dump_path"] == dump.resolve()
    assert restored["filestore_source"] == filestore
    assert restored["lifecycle_owner"] == "init"
    assert restored["target_major"] == 19
    assert restored["expected_dump_identity"] is not None


def test_migration_dump_path_selects_paired_upgrade_archive(
    migration_environment: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, environment = migration_environment
    dataset = workspace / "dataset"
    filestore = dataset / "odoo_filestore" / "filestore" / "odoo"
    filestore.mkdir(parents=True)
    (filestore / "attachment").write_text("data")
    archive = dataset / "upgraded.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("dump.sql", "select 1")
    environment.pop("GODOO_RUNTIME_SEED")
    environment.pop("GODOO_ORIGINAL_FILESTORE")
    environment["GODOO_DUMP_PATH"] = str(dataset)
    loaded: dict[str, object] = {}

    def deployment(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        cast(Callable[[GodooConfig], None], kwargs["seeder"])(config)
        return LifecycleOutcome.RESTORED, 0

    monkeypatch.setattr(runtime_init, "deployment_init", deployment)
    monkeypatch.setattr(runtime_init, "load_runtime_archive", lambda **kwargs: loaded.update(kwargs) or 0)
    result = CliRunner().invoke(_app(), ["--migration-dir", "transitions/16-19"], env=environment)

    assert result.exit_code == 0, result.output
    assert loaded["archive_path"] == archive.resolve()
    assert loaded["original_filestore"] == filestore


@pytest.mark.parametrize("modules", [None, "", "sale\nstock,sale\n"])
def test_migration_resolves_native_inputs_and_preserves_output_hooks(
    migration_environment: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch, modules: str | None
) -> None:
    """Selected module lists, native roots, and explicit hooks reach existing reconciliation."""
    directory, environment = migration_environment
    workspace = directory.parent.parent
    if modules is not None:
        (directory / "update_modules.txt").write_text(modules)
    (directory / "install_modules.txt").write_text("crm\ncontacts,crm\n")
    project_hooks = workspace / "project-hooks"
    project_hooks.mkdir()
    extra_upgrades = workspace / "extra-upgrades"
    extra_upgrades.mkdir()
    observed: dict[str, object] = {}

    def reconcile(_config: GodooConfig, updates: list[str], installs: list[str], **kwargs: object) -> int:
        observed.update(kwargs, updates=updates, installs=installs)
        return 0

    def reconcile_runtime(config: GodooConfig, **kwargs: object) -> int:
        return cast(Callable[[GodooConfig], int], kwargs["reconciler"])(config)

    def deployment(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        observed["hooks"] = kwargs["after_reconcile_dirs"]
        assert cast(Callable[[GodooConfig], int], kwargs["reconciler"])(config) == 0
        return LifecycleOutcome.RESTORED, 0

    monkeypatch.setattr(runtime_init, "reconcile_modules", reconcile)
    monkeypatch.setattr(runtime_init, "reconcile_runtime", reconcile_runtime)
    monkeypatch.setattr(runtime_init, "deployment_init", deployment)
    result = CliRunner().invoke(
        _app(),
        [
            "--migration-dir",
            str(directory.relative_to(workspace)),
            "--after-reconcile-dir",
            str(project_hooks),
            "--upgrade-path",
            str(extra_upgrades),
            "--expected-odoo-major",
            "19",
        ],
        env=environment,
    )

    assert result.exit_code == 0, result.output
    expected_updates = ["all"] if modules is None else ["sale", "stock"] if modules else []
    roots = [workspace / "migration" / "upgrade", directory / "upgrades", extra_upgrades]
    assert observed["updates"] == expected_updates
    assert observed["installs"] == ["crm", "contacts"]
    assert observed["pre_upgrade_scripts"] == ([directory / "pre-upgrade.py"] if expected_updates else None)
    assert observed["upgrade_path"] == (roots if expected_updates else None)
    assert observed["hooks"] == [directory / "after-reconcile", project_hooks]


@pytest.mark.parametrize("variable", CONFLICTING_ENVIRONMENT)
def test_migration_rejects_environment_injections_before_config(
    migration_environment: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """Generic environment defaults cannot change a selected migration."""
    directory, environment = migration_environment
    monkeypatch.setattr(
        runtime_init, "resolve_command_config", lambda **_kwargs: pytest.fail("config must be untouched")
    )
    environment[variable] = "injected"
    result = CliRunner().invoke(_app(), ["--migration-dir", str(directory)], env=environment)
    assert result.exit_code == 2, result.output
    assert variable in result.output


@pytest.mark.parametrize(
    "option", ["--update", "--install", "--pre-upgrade-script", "--after-bootstrap-dir", "--after-restore-dir"]
)
def test_migration_rejects_generic_cli_overrides_before_config(
    migration_environment: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch, option: str
) -> None:
    """Generic selection flags cannot replace transition-owned inputs."""
    directory, environment = migration_environment
    monkeypatch.setattr(
        runtime_init, "resolve_command_config", lambda **_kwargs: pytest.fail("config must be untouched")
    )
    result = CliRunner().invoke(_app(), ["--migration-dir", str(directory), option, "injected"], env=environment)
    assert result.exit_code == 2, result.output
    assert option in unstyle(result.output)


def test_migration_environment_supports_absolute_portable_directory(
    migration_environment: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absolute mounted transition works without conventional shared upgrade roots."""
    directory, environment = migration_environment
    workspace = directory.parent.parent
    (workspace / "migration" / "upgrade").rmdir()
    (directory / "upgrades").rmdir()
    environment["GODOO_MIGRATION_DIR"] = str(directory)
    monkeypatch.setattr(runtime_init, "deployment_init", lambda *_args, **_kwargs: (LifecycleOutcome.RESTORED, 0))
    result = CliRunner().invoke(_app(), [], env=environment)
    assert result.exit_code == 0, result.output
    assert resolve_migration_directory(directory, workspace_root=workspace).upgrade_paths == ()


@pytest.mark.parametrize("variable", ["GODOO_RUNTIME_SEED", "GODOO_ORIGINAL_FILESTORE"])
def test_migration_requires_explicit_restore_inputs(
    migration_environment: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """A selected transition cannot fall back to ordinary bootstrap."""
    directory, environment = migration_environment
    environment[variable] = ""
    monkeypatch.setattr(
        runtime_init, "resolve_command_config", lambda **_kwargs: pytest.fail("config must be untouched")
    )
    result = CliRunner().invoke(_app(), ["--migration-dir", str(directory)], env=environment)
    assert result.exit_code == 2, result.output
    assert "requires" in result.output
