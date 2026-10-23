"""Focused policy coverage for the runtime init command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.commands.runtime import init as runtime_init
from godoo_cli.models import GodooConfig
from godoo_cli.runtime.lifecycle import LifecycleOutcome


def _app() -> typer.Typer:
    app = typer.Typer()
    app.command()(runtime_init.deployment_init_odoo_runtime)
    return app


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "ODOO_MAIN_FOLDER": str(tmp_path / "odoo"),
        "ODOO_WORKSPACE_ADDON_LOCATION": str(tmp_path / "addons"),
        "ODOO_CONF_PATH": str(tmp_path / "odoo.conf"),
        "ODOO_DB_FILTER": ".*",
        "ODOO_MAIN_DB": "runtime",
        "ODOO_DB_USER": "odoo",
    }


def _config(tmp_path: Path) -> GodooConfig:
    return GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        odoo_conf_path=tmp_path / "odoo.conf",
        db_name="runtime",
        db_user="odoo",
        data_dir=tmp_path / "data",
    )


def _allow_ordinary_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Patch runtime work and retain calls that demonstrate ordinary init continued."""
    deployments: list[object] = []
    monkeypatch.setattr(runtime_init, "resolve_command_config", lambda **_kwargs: _config(tmp_path))
    monkeypatch.setattr(runtime_init, "require_odoo_version", lambda *_args: SimpleNamespace(major=19, raw="19.0"))
    monkeypatch.setattr(runtime_init, "x_sendfile_enabled", lambda *_args: False)
    monkeypatch.setattr(
        runtime_init,
        "deployment_init",
        lambda *_args, **_kwargs: deployments.append(True) or (LifecycleOutcome.READY, 0),
    )
    return deployments


def test_init_defaults_to_reconciliation_without_runtime_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default keeps the existing init behavior without inspecting runtime state."""
    deployments = _allow_ordinary_init(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_init, "inspect_runtime", lambda *_args: pytest.fail("status must not run"))

    result = CliRunner().invoke(_app(), [], env=_environment(tmp_path))

    assert result.exit_code == 0, result.output
    assert deployments == [True]


def test_if_needed_from_environment_inspects_the_resolved_readonly_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The environment option reads status with the resolved runtime target and exits when ready."""
    config = _config(tmp_path)
    resolved: list[dict[str, object]] = []
    inspected: list[tuple[object, Path, Path]] = []
    payload = {"state": "ready", "exit_code": 0}
    monkeypatch.setattr(
        runtime_init,
        "resolve_command_config",
        lambda **kwargs: resolved.append(kwargs) or config,
    )
    monkeypatch.setattr(
        runtime_init,
        "inspect_runtime",
        lambda connection, data_dir, provenance: inspected.append((connection, data_dir, provenance)) or (payload, 0),
    )
    monkeypatch.setattr(runtime_init, "resolve_runtime_dataset", lambda **_kwargs: pytest.fail("dataset must not run"))
    monkeypatch.setattr(runtime_init, "discover_hooks", lambda *_args: pytest.fail("hooks must not run"))
    monkeypatch.setattr(runtime_init, "deployment_init", lambda *_args, **_kwargs: pytest.fail("init must not run"))

    result = CliRunner().invoke(_app(), [], env=_environment(tmp_path) | {"GODOO_INIT_IF_EMPTY": "true"})

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload
    assert len(resolved) == 1
    assert resolved[0]["odoo_main_path"] == tmp_path / "odoo"
    assert resolved[0]["workspace_addon_path"] == tmp_path / "addons"
    assert resolved[0]["odoo_conf_path"] == tmp_path / "odoo.conf"
    assert resolved[0]["db_name"] == "runtime"
    connection, data_dir, provenance = inspected[0]
    assert connection.db_name == config.db_name
    assert connection.readonly is True
    assert data_dir == config.data_dir
    assert provenance == Path("/odoo/godoo-source-provenance.json")


def test_no_if_needed_overrides_true_environment_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit CLI disable retains normal reconciliation when the environment enables probing."""
    deployments = _allow_ordinary_init(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_init, "inspect_runtime", lambda *_args: pytest.fail("status must not run"))

    result = CliRunner().invoke(
        _app(), ["--no-if-needed"], env=_environment(tmp_path) | {"GODOO_INIT_IF_EMPTY": "true"}
    )

    assert result.exit_code == 0, result.output
    assert deployments == [True]


@pytest.mark.parametrize("status", [20, 21, 23])
def test_if_needed_continues_for_initializable_or_incomplete_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Missing, empty, and incomplete states continue through normal initialization."""
    deployments = _allow_ordinary_init(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_init, "inspect_runtime", lambda *_args: ({"exit_code": status}, status))

    result = CliRunner().invoke(_app(), ["--if-needed"], env=_environment(tmp_path))

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"exit_code": status}
    assert deployments == [True]


@pytest.mark.parametrize("status", [1, 22, 37])
def test_if_needed_returns_non_recoverable_status_without_initializing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Nonrecoverable status codes are returned before dataset or lifecycle work starts."""
    config = _config(tmp_path)
    payload = {"state": "unavailable", "exit_code": status}
    monkeypatch.setattr(runtime_init, "resolve_command_config", lambda **_kwargs: config)
    monkeypatch.setattr(runtime_init, "inspect_runtime", lambda *_args: (payload, status))
    monkeypatch.setattr(runtime_init, "resolve_runtime_dataset", lambda **_kwargs: pytest.fail("dataset must not run"))
    monkeypatch.setattr(runtime_init, "discover_hooks", lambda *_args: pytest.fail("hooks must not run"))
    monkeypatch.setattr(runtime_init, "deployment_init", lambda *_args, **_kwargs: pytest.fail("init must not run"))

    result = CliRunner().invoke(_app(), ["--if-needed"], env=_environment(tmp_path))

    assert result.exit_code == status, result.output
    assert json.loads(result.output) == payload


def test_ready_if_needed_skips_invalid_lifecycle_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ready runtime exits before dataset, hook, and demo-seed validation."""
    config = _config(tmp_path)
    monkeypatch.setattr(runtime_init, "resolve_command_config", lambda **_kwargs: config)
    monkeypatch.setattr(runtime_init, "inspect_runtime", lambda *_args: ({"exit_code": 0}, 0))
    monkeypatch.setattr(runtime_init, "resolve_runtime_dataset", lambda **_kwargs: pytest.fail("dataset must not run"))
    monkeypatch.setattr(runtime_init, "discover_hooks", lambda *_args: pytest.fail("hooks must not run"))
    monkeypatch.setattr(runtime_init, "deployment_init", lambda *_args, **_kwargs: pytest.fail("init must not run"))

    result = CliRunner().invoke(
        _app(),
        [
            "--if-needed",
            "--seed",
            str(tmp_path / "missing.zip"),
            "--dump-path",
            str(tmp_path / "missing-dataset"),
            "--hooks-dir",
            str(tmp_path / "missing-hooks"),
            "--odoo-demo",
        ],
        env=_environment(tmp_path),
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"exit_code": 0}


def test_demo_clears_dataset_inputs_before_dataset_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Demo bootstrap ignores fixed dataset and original-filestore inputs before selection."""
    deployments = _allow_ordinary_init(tmp_path, monkeypatch)
    observed: dict[str, Path | None] = {}
    monkeypatch.setattr(
        runtime_init,
        "resolve_runtime_dataset",
        lambda **kwargs: observed.update(kwargs) or None,
    )

    result = CliRunner().invoke(
        _app(),
        [
            "--odoo-demo",
            "--dump-path",
            str(tmp_path / "dataset"),
            "--original-filestore",
            str(tmp_path / "original-filestore"),
        ],
        env=_environment(tmp_path),
    )

    assert result.exit_code == 0, result.output
    assert observed == {"dataset_root": None, "seed": None, "original_filestore": None}
    assert deployments == [True]


def test_demo_environment_clears_selection_inputs_without_mutating_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Demo mode clears local selection values while leaving process environment inputs available."""
    deployments = _allow_ordinary_init(tmp_path, monkeypatch)
    observed: dict[str, Path | None] = {}
    resolver_environment: dict[str, str | None] = {}

    def resolve_dataset(**kwargs: Path | None) -> None:
        observed.update(kwargs)
        resolver_environment.update(
            dump_path=os.environ.get("GODOO_DUMP_PATH"),
            original_filestore=os.environ.get("GODOO_ORIGINAL_FILESTORE"),
        )

    monkeypatch.setattr(runtime_init, "resolve_runtime_dataset", resolve_dataset)
    environment = _environment(tmp_path) | {
        "GODOO_RUNTIME_DEMO": "true",
        "GODOO_DUMP_PATH": str(tmp_path / "dataset"),
        "GODOO_ORIGINAL_FILESTORE": str(tmp_path / "original-filestore"),
    }

    result = CliRunner().invoke(_app(), [], env=environment)

    assert result.exit_code == 0, result.output
    assert observed == {"dataset_root": None, "seed": None, "original_filestore": None}
    assert resolver_environment == {
        "dump_path": str(tmp_path / "dataset"),
        "original_filestore": str(tmp_path / "original-filestore"),
    }
    assert deployments == [True]


def test_non_demo_preserves_dataset_selection_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Normal initialization sends the selected dataset, seed, and filestore to the resolver."""
    deployments = _allow_ordinary_init(tmp_path, monkeypatch)
    observed: dict[str, Path | None] = {}
    monkeypatch.setattr(runtime_init, "resolve_runtime_dataset", lambda **kwargs: observed.update(kwargs) or None)
    dataset = tmp_path / "dataset"
    seed = tmp_path / "seed.zip"
    original_filestore = tmp_path / "original-filestore"

    result = CliRunner().invoke(
        _app(),
        [
            "--dump-path",
            str(dataset),
            "--seed",
            str(seed),
            "--original-filestore",
            str(original_filestore),
        ],
        env=_environment(tmp_path),
    )

    assert result.exit_code == 0, result.output
    assert observed == {
        "dataset_root": dataset,
        "seed": seed,
        "original_filestore": original_filestore,
    }
    assert deployments == [True]


def test_if_needed_reuses_real_resolved_target_for_status_and_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status and initialization share the target resolved from explicit CLI connection inputs."""
    inspected: list[tuple[object, Path]] = []
    initialized: list[GodooConfig] = []
    data_dir = tmp_path / "selected-data"
    monkeypatch.setattr(
        runtime_init,
        "inspect_runtime",
        lambda connection, selected_data_dir, _provenance: (
            inspected.append((connection, selected_data_dir)) or ({"exit_code": 20}, 20)
        ),
    )
    monkeypatch.setattr(runtime_init, "require_odoo_version", lambda *_args: SimpleNamespace(major=19, raw="19.0"))
    monkeypatch.setattr(runtime_init, "x_sendfile_enabled", lambda *_args: False)
    monkeypatch.setattr(
        runtime_init,
        "deployment_init",
        lambda config, **_kwargs: initialized.append(config) or (LifecycleOutcome.READY, 0),
    )

    result = CliRunner().invoke(
        _app(),
        [
            "--if-needed",
            "--db-name",
            "selected-runtime",
            "--db-host",
            "postgres.example.test",
            "--db-port",
            "5544",
            "--data-dir",
            str(data_dir),
        ],
        env=_environment(tmp_path),
    )

    assert result.exit_code == 0, result.output
    connection, selected_data_dir = inspected[0]
    assert connection.db_name == "selected-runtime"
    assert connection.hostname == "postgres.example.test"
    assert connection.port == 5544
    assert connection.readonly is True
    assert selected_data_dir == data_dir
    assert len(initialized) == 1
    assert initialized[0].db_name == connection.db_name
    assert initialized[0].db_host == connection.hostname
    assert initialized[0].db_port == connection.port
    assert initialized[0].data_dir == selected_data_dir
