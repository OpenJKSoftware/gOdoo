"""Regression coverage for operation-local executable version reuse."""

from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock, patch

import pytest

from godoo_cli.database.state import DbBootstrapStatus
from godoo_cli.models import GodooConfig
from godoo_cli.runtime import odoo


def _addon(root: Path, name: str, requirement: str | None = None) -> None:
    module = root / name
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text(
        repr({"name": name, "depends": [], "external_dependencies": {"python": [requirement] if requirement else []}})
    )


def _config(root: Path, version: str = "Odoo 19.0") -> GodooConfig:
    source = root / "odoo"
    _addon(source / "addons", "base")
    (source / "odoo-bin").write_text(version)
    workspace = root / "workspace"
    workspace.mkdir()
    return GodooConfig(
        odoo_install_folder=source,
        odoo_conf_path=root / "odoo.conf",
        workspace_addon_path=workspace,
        thirdparty_addon_path=root / "thirdparty",
        data_dir=root / "data",
        db_name="selected-db",
    )


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GodooConfig:
    """Keep real version parsing and module discovery independent of PostgreSQL and uv."""
    environment = tmp_path / "venv"
    (environment / "bin").mkdir(parents=True)
    (environment / "bin" / "python").touch()
    monkeypatch.setenv("VIRTUAL_ENV", str(environment))
    monkeypatch.setattr(odoo, "classify_bootstrap_state", lambda _connection: DbBootstrapStatus.BOOTSTRAPPED)
    monkeypatch.setattr(odoo, "base_module_major", lambda _connection: 19)
    monkeypatch.setattr(odoo, "_database_modules", lambda _config: {})
    return _config(tmp_path)


@pytest.mark.parametrize("operation", ["preflight_for_config", "dependency_requirements"])
def test_dependency_operation_probes_executable_once(runtime: GodooConfig, operation: str) -> None:
    """Database validation and both module registries share one executable result."""
    with patch.object(odoo, "run_odoo_command", return_value=CompletedProcess([], 0, stdout="Odoo 19.0")) as probe:
        getattr(odoo, operation)(runtime)

    probe.assert_called_once_with([str(runtime.odoo_bin_path.absolute()), "--version"], capture_output=True, text=True)


def test_preflight_reuses_version_with_config_and_argv_precedence(runtime: GodooConfig, tmp_path: Path) -> None:
    """Selected argv roots and data addons still determine installed requirements."""
    configured, selected = tmp_path / "configured", tmp_path / "selected"
    _addon(configured, "selected_module", "configured-pkg")
    _addon(selected, "selected_module", "argv-pkg")
    data = tmp_path / "selected-data"
    _addon(data / "addons" / "19.0", "data_module", "data-pkg")
    child_config = tmp_path / "child.conf"
    child_config.write_text(f"[options]\naddons_path = {configured}\ndata_dir = {tmp_path / 'wrong-data'}\n")
    arguments = [
        "-c",
        str(child_config),
        "--addons-path",
        str(selected),
        "--data-dir",
        str(data),
        "--init",
        "selected_module,data_module",
    ]
    with (
        patch.object(odoo, "run_odoo_command", return_value=CompletedProcess([], 0, stdout="Odoo 19.0")) as probe,
        patch.object(odoo, "subprocess") as process,
    ):
        odoo.preflight_for_config(runtime, arguments)

    probe.assert_called_once()
    command = process.run.call_args.args[0]
    assert command[-2:] == ["argv-pkg", "data-pkg"]
    assert "configured-pkg" not in command
    process.run.assert_called_once()


@pytest.mark.parametrize("include_dependencies", [True, False])
@pytest.mark.parametrize("output", ["Odoo 15.0", "unparseable version"])
def test_preflight_keeps_executable_version_failures(
    runtime: GodooConfig, include_dependencies: bool, output: str
) -> None:
    """Unsupported and malformed executable versions fail before dependency installation."""
    with (
        patch.object(odoo, "run_odoo_command", return_value=CompletedProcess([], 0, stdout=output)) as probe,
        patch.object(odoo, "subprocess") as process,
        pytest.raises(odoo.OdooVersionError),
    ):
        odoo.preflight_for_config(runtime, include_module_dependencies=include_dependencies)

    probe.assert_called_once()
    process.run.assert_not_called()


def test_preflight_keeps_database_major_validation(runtime: GodooConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reused executable result still rejects an incompatible initialized database."""
    monkeypatch.setattr(odoo, "base_module_major", lambda _connection: 18)
    with (
        patch.object(odoo, "run_odoo_command", return_value=CompletedProcess([], 0, stdout="Odoo 19.0")) as probe,
        patch.object(odoo, "subprocess") as process,
        pytest.raises(RuntimeError, match=r"selected-db.*contains Odoo 18.*godoo db load"),
    ):
        odoo.preflight_for_config(runtime)

    probe.assert_called_once()
    process.run.assert_not_called()


def test_preflight_without_module_dependencies_still_checks_executable(runtime: GodooConfig) -> None:
    """Core-only preparation retains its executable compatibility check."""
    with patch.object(odoo, "run_odoo_command", return_value=CompletedProcess([], 0, stdout="Odoo 19.0")) as probe:
        odoo.preflight_for_config(runtime, include_module_dependencies=False)

    probe.assert_called_once()


def test_next_operation_rechecks_changed_source(runtime: GodooConfig) -> None:
    """Replacing source at the same path changes the selected versioned addon root."""
    for major in (19, 18):
        _addon(runtime.data_dir / "addons" / f"{major}.0", "versioned", f"pkg{major}")
    with patch.object(odoo, "run_odoo_command") as probe:
        probe.side_effect = lambda command, **_kwargs: CompletedProcess([], 0, stdout=Path(command[0]).read_text())
        assert odoo.dependency_requirements(runtime, ["--init", "versioned"]) == ["pkg19"]
        runtime.odoo_bin_path.write_text("Odoo 18.0")
        assert odoo.dependency_requirements(runtime, ["--init", "versioned"]) == ["pkg18"]

    assert probe.call_count == 2


def test_next_preflight_rechecks_selected_installation(
    runtime: GodooConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second operation validates its own target instead of reusing the first version."""
    other = _config(tmp_path / "other", "Odoo 18.0")
    target = replace(runtime, odoo_install_folder=other.odoo_install_folder)
    with patch.object(odoo, "run_odoo_command") as probe:
        probe.side_effect = lambda command, **_kwargs: CompletedProcess([], 0, stdout=Path(command[0]).read_text())
        odoo.preflight_for_config(runtime)
        monkeypatch.setattr(odoo, "base_module_major", lambda _connection: 18)
        odoo.preflight_for_config(target)

    assert [call.args[0][0] for call in probe.call_args_list] == [
        str(runtime.odoo_bin_path.absolute()),
        str(target.odoo_bin_path.absolute()),
    ]


@pytest.mark.parametrize("argument", ["--help", "-h", "--version"])
def test_help_and_version_skip_preflight_probes(runtime: GodooConfig, argument: str) -> None:
    """Odoo informational commands bypass preflight and its executable probe."""
    with patch.object(odoo, "run_odoo_command") as probe:
        odoo.preflight_for_config(runtime, [argument])

    probe.assert_not_called()


@pytest.mark.parametrize("database", [None, "selected-db"])
def test_missing_executable_preserves_database_guard(runtime: GodooConfig, database: str | None) -> None:
    """Source-only discovery stays possible while configured databases still require a runtime."""
    runtime.odoo_bin_path.unlink()
    config = replace(runtime, db_name=database)
    probe = Mock(side_effect=FileNotFoundError("missing odoo-bin"))
    with patch.object(odoo, "run_odoo_command", probe):
        if database:
            with pytest.raises(odoo.OdooVersionError, match="missing odoo-bin"):
                odoo.preflight_for_config(config)
            probe.assert_called_once()
        else:
            odoo.preflight_for_config(config)
            probe.assert_not_called()
