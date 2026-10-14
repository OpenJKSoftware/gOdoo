"""Migration selection feeds the existing init lifecycle without runtime side effects."""

import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.commands.runtime import init as runtime_init
from godoo_cli.database.state import DbBootstrapStatus
from godoo_cli.models import GodooConfig
from godoo_cli.runtime import lifecycle as runtime_lifecycle
from godoo_cli.runtime.lifecycle import LifecycleOutcome
from godoo_cli.runtime.locks import read_runtime_lifecycle, runtime_readiness_marker
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
        observed["plan"] = kwargs["lifecycle_plan"]
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
    identity = cast(dict[str, object], cast(dict[str, object], observed["plan"])["identity"])
    assert cast(dict[str, object], identity["runtime"])["expected_major"] == 19
    assert identity["migration"] == {
        "directory": str(directory),
        "source_major": 16,
        "target_major": 19,
        "pre_upgrade_script": str(directory / "pre-upgrade.py"),
        "upgrade_roots": [str(root) for root in roots],
    }


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
    assert option in result.output


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "name",
        "descending",
        "pre-script",
        "hooks",
        "nested-hooks",
        "seed",
        "filestore",
        "demo",
        "major",
        "root",
    ],
)
def test_migration_preflight_rejects_invalid_inputs_without_deployment(
    migration_environment: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    """Invalid transition inputs fail before any lifecycle execution callback."""
    directory, environment = migration_environment
    arguments = ["--migration-dir", str(directory)]
    if invalid in {"missing", "name", "descending"}:
        selected = directory.parent / {"missing": "17-19", "name": "transition", "descending": "19-16"}[invalid]
        if invalid != "missing":
            directory.rename(selected)
        arguments = ["--migration-dir", str(selected)]
    elif invalid == "pre-script":
        (directory / "pre-upgrade.py").unlink()
    elif invalid in {"hooks", "nested-hooks"}:
        hook = directory / "after-reconcile" / "01-finish.py"
        hook.unlink()
        if invalid == "nested-hooks":
            (hook.parent / "nested").mkdir()
            (hook.parent / "nested" / "hook.py").write_text("pass\n")
    elif invalid == "seed":
        Path(environment["GODOO_RUNTIME_SEED"]).write_text("not a ZIP")
    elif invalid == "filestore":
        (Path(environment["GODOO_ORIGINAL_FILESTORE"]) / "attachment").unlink()
    elif invalid == "demo":
        arguments.append("--odoo-demo")
    elif invalid == "major":
        arguments.extend(["--expected-odoo-major", "18"])
    else:
        arguments.extend(["--upgrade-path", str(directory / "missing-upgrades")])
    monkeypatch.setattr(
        runtime_init, "deployment_init", lambda *_args, **_kwargs: pytest.fail("deployment must not run")
    )
    result = CliRunner().invoke(_app(), arguments, env=environment)
    assert result.exit_code == 2, result.output


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


@pytest.mark.parametrize("additional", [False, True])
def test_migration_rejects_nonfile_hook_entries_before_deployment(
    migration_environment: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch, additional: bool
) -> None:
    """Neither transition nor added project hooks can defer invalid entries until execution."""
    directory, environment = migration_environment
    arguments = ["--migration-dir", str(directory)]
    hook_dir = directory / "after-reconcile"
    if additional:
        hook_dir = directory.parent / "project-hooks"
        hook_dir.mkdir()
        arguments.extend(["--after-reconcile-dir", str(hook_dir)])
    (hook_dir / "not-a-file.py").mkdir()
    monkeypatch.setattr(
        runtime_init, "deployment_init", lambda *_args, **_kwargs: pytest.fail("deployment must not run")
    )
    result = CliRunner().invoke(_app(), arguments, env=environment)
    assert result.exit_code == 2, result.output
    assert "not a Python file" in result.output


@pytest.mark.parametrize("changed_input", [None, "modules", "roots", "directory", "hooks"])
def test_migration_pending_plan_binds_selection_even_with_empty_updates(
    migration_environment: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    changed_input: str | None,
) -> None:
    """Resume pending hooks only when selected migration inputs still match."""
    directory, environment = migration_environment
    (directory / "update_modules.txt").write_text("")
    calls: list[str] = []
    hook_attempts = 0

    def hook(_config: GodooConfig, _script: Path) -> int:
        nonlocal hook_attempts
        calls.append("hook")
        hook_attempts += 1
        return 1 if hook_attempts == 1 else 0

    def deployment(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        return runtime_lifecycle.deployment_init(
            config,
            seed_requested=True,
            seeder=lambda _config: pytest.fail("ready database must not be seeded"),
            ensure=lambda _config: pytest.fail("ready database must not be bootstrapped"),
            preparer=lambda _config: calls.append("prepare"),
            preflight=lambda _config: calls.append("preflight"),
            reconciler=lambda _config: calls.append("reconcile") or 0,
            status_getter=lambda _connection: DbBootstrapStatus.BOOTSTRAPPED,
            after_reconcile_dirs=cast(list[Path], kwargs["after_reconcile_dirs"]),
            hook_runner=hook,
            lifecycle_plan=cast(dict[str, object], kwargs["lifecycle_plan"]),
        )

    monkeypatch.setattr(runtime_init, "deployment_init", deployment)
    monkeypatch.setattr(runtime_lifecycle, "require_runtime_database_major", lambda _config: 19)
    result = CliRunner().invoke(_app(), ["--migration-dir", str(directory)], env=environment)
    assert result.exit_code == 1, result.output
    assert calls == ["preflight", "prepare", "reconcile", "hook"]
    calls.clear()
    if changed_input == "modules":
        (directory / "update_modules.txt").write_text("sale\n")
    elif changed_input == "roots":
        (directory / "upgrades").rmdir()
    elif changed_input == "directory":
        selected = directory.parent / "17-19"
        directory.rename(selected)
        directory = selected
    elif changed_input == "hooks":
        (directory / "after-reconcile" / "02-added.py").write_text("pass\n")
    result = CliRunner().invoke(_app(), ["--migration-dir", str(directory)], env=environment)
    if changed_input is None:
        assert result.exit_code == 0, result.output
        assert calls == ["preflight", "prepare", "hook"]
    else:
        assert result.exit_code == 1, result.output
        assert "Pending lifecycle plan differs" in caplog.text
        assert calls == []


def test_migration_restore_dependency_failure_resumes_without_reseeding(
    migration_environment: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dependency found after restore leaves the selected plan pending at that phase."""
    directory, environment = migration_environment
    status = DbBootstrapStatus.NO_DB
    restored_dependency_missing = True
    calls: list[str] = []

    def load_archive(**_kwargs: object) -> int:
        nonlocal status
        calls.append("seed")
        status = DbBootstrapStatus.BOOTSTRAPPED
        return 0

    def preflight(_config: GodooConfig, _updates: list[str], _installs: list[str], **kwargs: object) -> None:
        assert kwargs["ignore_missing_installed_modules"] is True
        calls.append("preflight" if status is DbBootstrapStatus.NO_DB else "restored-preflight")
        if status is DbBootstrapStatus.BOOTSTRAPPED and restored_dependency_missing:
            message = "Missing dependency discovered after restore"
            raise RuntimeError(message)

    def deployment(config: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        return runtime_lifecycle.deployment_init(
            config,
            seed_requested=cast(bool, kwargs["seed_requested"]),
            seeder=cast(Callable[[GodooConfig], None], kwargs["seeder"]),
            ensure=cast(Callable[[GodooConfig], bool], kwargs["ensure"]),
            preparer=cast(Callable[[GodooConfig], None], kwargs["preparer"]),
            preflight=cast(Callable[[GodooConfig], None], kwargs["preflight"]),
            post_restore_preflight=cast(Callable[[GodooConfig], None], kwargs["post_restore_preflight"]),
            reconciler=cast(Callable[[GodooConfig], int], kwargs["reconciler"]),
            status_getter=lambda _connection: status,
            after_reconcile_dirs=cast(list[Path], kwargs["after_reconcile_dirs"]),
            hook_runner=cast(Callable[[GodooConfig, Path], int], kwargs["hook_runner"]),
            lifecycle_plan=cast(dict[str, object], kwargs["lifecycle_plan"]),
        )

    monkeypatch.setattr(runtime_init, "deployment_init", deployment)
    monkeypatch.setattr(runtime_init, "load_runtime_archive", load_archive)
    monkeypatch.setattr(runtime_init, "prepare_runtime", lambda *_args, **_kwargs: calls.append("prepare"))
    monkeypatch.setattr(runtime_init, "preflight_reconcile_dependencies", preflight)
    monkeypatch.setattr(runtime_init, "reconcile_runtime", lambda *_args, **_kwargs: calls.append("reconcile") or 0)
    monkeypatch.setattr(runtime_init, "run_lifecycle_hook", lambda *_args: calls.append("hook") or 0)
    monkeypatch.setattr(runtime_lifecycle, "require_runtime_database_major", lambda _config: 19)
    arguments = ["--migration-dir", str(directory)]
    result = CliRunner().invoke(_app(), arguments, env=environment)

    assert result.exit_code == 1, result.output
    assert "Missing dependency discovered after restore" in caplog.text
    assert calls == ["preflight", "prepare", "seed", "restored-preflight"]
    marker = runtime_readiness_marker(directory.parent.parent / "data", "runtime")
    pending = read_runtime_lifecycle(marker, "runtime")
    assert pending is not None
    assert pending["pending_phase"] == "after-restore"
    assert pending["outcome"] == "restored"
    identity = cast(dict[str, object], cast(dict[str, object], pending["plan"])["identity"])
    assert cast(dict[str, object], identity["migration"])["directory"] == str(directory)

    restored_dependency_missing = False
    calls.clear()
    result = CliRunner().invoke(_app(), arguments, env=environment)

    assert result.exit_code == 0, result.output
    assert calls == ["restored-preflight", "prepare", "restored-preflight", "reconcile", "hook"]
    assert not marker.exists()
