"""Tests deployment configuration of Odoo's report asset URL."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.commands.runtime import init as runtime_init
from godoo_cli.models import GodooConfig
from godoo_cli.runtime import odoo
from godoo_cli.runtime.lifecycle import LifecycleOutcome


def _config(tmp_path: Path) -> GodooConfig:
    return GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        db_name="runtime",
    )


def test_deployment_init_requires_report_url_with_persisted_x_sendfile(
    tmp_path: Path,
) -> None:
    """Reject persisted X-Sendfile when no report asset URL is configured."""
    app = typer.Typer()
    app.command()(runtime_init.deployment_init_odoo_runtime)
    config_path = tmp_path / "odoo.conf"
    config_path.write_text("[options]\nx_sendfile = True\n")

    result = CliRunner().invoke(
        app,
        [],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_CONF_PATH": str(config_path),
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )

    assert result.exit_code == 2
    assert "GODOO_REPORT_URL" in result.output


def test_deployment_init_allows_explicit_false_over_persisted_x_sendfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Let explicit false override persisted X-Sendfile for the URL guard."""
    app = typer.Typer()
    app.command()(runtime_init.deployment_init_odoo_runtime)
    config_path = tmp_path / "odoo.conf"
    config_path.write_text("[options]\nx_sendfile = True\n")
    monkeypatch.setattr(
        runtime_init,
        "deployment_init",
        lambda *_args, **_kwargs: (LifecycleOutcome.READY, 0),
    )

    result = CliRunner().invoke(
        app,
        ["--no-x-sendfile"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_CONF_PATH": str(config_path),
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )

    assert result.exit_code == 0, result.output


def test_set_report_url_commits_parameter_through_odoo_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Persist report.url through Odoo so the ORM writes the parameter."""
    observed: dict[str, object] = {}

    def run_command(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(odoo, "require_odoo_version", lambda *_args: None)
    monkeypatch.setattr(odoo, "preflight_for_config", lambda *_args: None)
    monkeypatch.setattr(odoo, "run_odoo_command", run_command)

    odoo.set_report_url(_config(tmp_path), "https://reports.example.test")

    assert observed["input"] == (
        "env['ir.config_parameter'].sudo().set_param('report.url', 'https://reports.example.test')\nenv.cr.commit()\n"
    )
    assert observed["text"] is True
    assert "shell" in observed["command"]


def test_set_report_url_reports_shell_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Surface a failed shell command so deployment init cannot hide it."""
    monkeypatch.setattr(odoo, "require_odoo_version", lambda *_args: None)
    monkeypatch.setattr(odoo, "preflight_for_config", lambda *_args: None)
    monkeypatch.setattr(
        odoo,
        "run_odoo_command",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=17),
    )

    with pytest.raises(RuntimeError, match=re.escape("report.url (exit code 17)")):
        odoo.set_report_url(_config(tmp_path), "https://reports.example.test")
