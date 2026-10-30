"""Every server start initializes one target before launching it."""

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.models import GodooConfig

start = importlib.import_module("godoo_cli.commands.runtime.start")


@pytest.mark.parametrize("init_status", [0, 7])
def test_start_resolves_once_routes_options_and_stops_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_status: int
) -> None:
    config = GodooConfig(
        odoo_install_folder=tmp_path,
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        db_name="runtime",
    )
    resolved = []
    calls = []

    def resolve(**options: Any) -> GodooConfig:
        resolved.append(options)
        return config

    def initialize(resolve_config: Callable[[], GodooConfig], options: start.InitOptions) -> int:
        assert resolve_config() is config
        assert options.if_needed is False
        assert options.update_changed is True
        assert options.extra_bootstrap_args == ["--without-demo"]
        assert not hasattr(options, "debug_listen")
        calls.append("init")
        return init_status

    def launch(target: GodooConfig, **options: Any) -> int:
        assert target is config
        assert options["debug_listen"] == "0.0.0.0:5678"
        assert options["debug_wait_for_client"] is True
        assert options["extra_args"] == ["--workers=0"]
        assert "extra_bootstrap_args" not in options
        calls.append("launch")
        return 0

    monkeypatch.setattr(start, "resolve_command_config", resolve)
    monkeypatch.setattr(start, "initialize_odoo_runtime", initialize)
    monkeypatch.setattr(start, "launch_prepared_runtime", launch)
    # Service option routing uses the original declarations, not these test doubles.
    app = typer.Typer()
    app.command()(start.start_odoo_runtime)
    result = CliRunner().invoke(
        app,
        [
            "--update-changed",
            "--extra-bootstrap-args=--without-demo",
            "--extra-args=--workers=0",
            "--debug-listen",
            "0.0.0.0:5678",
            "--debug-wait-for-client",
        ],
        env={
            "ODOO_MAIN_FOLDER": str(tmp_path),
            "ODOO_WORKSPACE_ADDON_LOCATION": str(tmp_path / "addons"),
            "ODOO_CONF_PATH": str(tmp_path / "odoo.conf"),
            "ODOO_DB_FILTER": "runtime",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
            "GODOO_INIT_IF_EMPTY": "true",
        },
    )
    assert result.exit_code == init_status, result.exception
    assert len(resolved) == 1
    assert resolved[0]["arguments"] == ["--workers=0"]
    assert calls == (["init", "launch"] if init_status == 0 else ["init"])
