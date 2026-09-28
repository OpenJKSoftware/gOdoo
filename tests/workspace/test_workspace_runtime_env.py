"""CLI contract for host-resolved development runtime paths."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from godoo_cli.commands.workspace import workspace_cli_app
from godoo_cli.workspace.types import WorkspaceError


def test_runtime_env_prints_shell_safe_assignments(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Values with shell metacharacters remain safe to pass through eval."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    monkeypatch.setattr(
        "godoo_cli.commands.workspace.configure.runtime_environment",
        lambda _settings: {
            "GODOO_SOURCES_ROOT": "/sources",
            "GODOO_RUNTIME_ODOO_PATH": "/sources/Odoo's source",
            "GODOO_RUNTIME_ADDON_PATHS": "/sources/a b:/sources/$addons",
        },
    )
    monkeypatch.setattr(
        "godoo_cli.commands.workspace.common._sources_root",
        lambda _sources_root: tmp_path / "sources",
    )

    result = CliRunner().invoke(workspace_cli_app(), ["runtime-env"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "GODOO_SOURCES_ROOT=/sources",
        "GODOO_RUNTIME_ODOO_PATH='/sources/Odoo'\"'\"'s source'",
        "GODOO_RUNTIME_ADDON_PATHS='/sources/a b:/sources/$addons'",
    ]


def test_runtime_env_reports_workspace_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Invalid or unavailable host workspaces do not emit partial assignments."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    monkeypatch.setattr(
        "godoo_cli.commands.workspace.configure.runtime_environment",
        lambda _settings: (_ for _ in ()).throw(WorkspaceError("source missing")),
    )
    monkeypatch.setattr(
        "godoo_cli.commands.workspace.common._sources_root",
        lambda _sources_root: tmp_path / "sources",
    )

    result = CliRunner().invoke(workspace_cli_app(), ["runtime-env"])

    assert result.exit_code == 1
    assert "GODOO_RUNTIME_ODOO_PATH=" not in result.output
    assert "Workspace runtime environment failed: source missing" in result.output


def test_workspace_check_accepts_sources_only_option(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Production preflight can skip generated editor checks with --sources-only."""
    settings = object()
    observed: dict[str, object] = {}
    monkeypatch.setattr("godoo_cli.commands.workspace.common._settings", lambda *_args: settings)

    def check(_settings: object, *, sources_only: bool = False) -> dict[str, str]:
        observed["sources_only"] = sources_only
        return {"project_root": str(tmp_path)}

    monkeypatch.setattr("godoo_cli.commands.workspace.sync.check_workspace", check)
    runner = CliRunner()
    inherited = runner.invoke(
        workspace_cli_app(),
        ["check"],
        env={"GODOO_SOURCES_ROOT": str(tmp_path), "GODOO_CHECK_SOURCES_ONLY": "1"},
    )
    assert inherited.exit_code == 0, inherited.output
    assert observed["sources_only"] is False

    result = runner.invoke(
        workspace_cli_app(),
        ["check", "--sources-only"],
        env={"GODOO_SOURCES_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0, result.output
    assert observed["sources_only"] is True
    assert "Workspace sources are current" in result.output
