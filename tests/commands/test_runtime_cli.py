"""Tests for the clean runtime CLI namespace."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click import unstyle
from typer import BadParameter
from typer.testing import CliRunner

from godoo_cli import __about__
from godoo_cli.commands.configuration import resolve_command_config
from godoo_cli.commands.root import main_cli
from godoo_cli.commands.run import _uses_managed_server_config, run_odoo
from godoo_cli.workspace.types import WorkspaceError


def _command_config(tmp_path: Path):
    """Resolve a minimal command configuration for source-layout tests."""
    return resolve_command_config(
        odoo_main_path=tmp_path / "canonical-odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "workspace-addons",
    )


def test_command_config_derives_thirdparty_from_odoo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the selected Odoo root's sibling for third-party addons by default."""
    monkeypatch.delenv("GODOO_SOURCES_ROOT", raising=False)
    monkeypatch.delenv("GODOO_RUNTIME_ODOO_PATH", raising=False)
    monkeypatch.delenv("GODOO_RUNTIME_ADDON_PATHS", raising=False)
    monkeypatch.delenv("GODOO_RUNTIME_MATERIALIZED", raising=False)
    config = resolve_command_config(
        odoo_main_path=tmp_path / "image" / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
    )

    assert config.thirdparty_addon_path == tmp_path / "image" / "thirdparty"


def test_command_config_reports_invalid_mounted_source_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Workspace validation failures remain actionable at the CLI boundary."""
    project = tmp_path / "project"
    sources_root = tmp_path / "sources"
    project.mkdir()
    sources_root.mkdir()
    manifest = project / "odoo_manifest.yml"
    manifest.touch()
    monkeypatch.chdir(project)
    monkeypatch.setenv("GODOO_SOURCES_ROOT", str(sources_root))
    monkeypatch.setenv("ODOO_MANIFEST", str(manifest))
    with (
        patch(
            "godoo_cli.workspace.resolve_workspace_sources",
            side_effect=WorkspaceError("selected Odoo worktree is missing"),
        ),
        pytest.raises(BadParameter, match="Could not resolve development sources"),
    ):
        _command_config(project)


def test_runtime_exposes_canonical_commands():
    """Guards the contract that runtime exposes canonical commands."""
    result = CliRunner().invoke(main_cli(), ["runtime", "--help"], terminal_width=220)
    output = unstyle(result.output)
    assert result.exit_code == 0
    commands = set(re.findall(r"^│ ([\w-]+)\s", output, re.MULTILINE)) - {"--help"}
    assert commands == {"init", "launch", "shell", "shell-script", "status"}


def test_database_help_preserves_public_database_commands():
    """Guards the contract that database help preserves public database commands."""
    result = CliRunner().invoke(main_cli(), ["db", "--help"], terminal_width=220)
    output = unstyle(result.output)
    assert result.exit_code == 0
    commands = set(re.findall(r"^│ ([\w-]+)\s", output, re.MULTILINE)) - {"--help"}
    assert commands == {
        "prepare",
        "backup",
        "restore",
        "clone",
        "reset",
        "status",
        "set-passwords",
        "login",
        "query",
        "installed-modules",
    }


def test_root_help_exposes_clean_command_tree():
    """Guards the contract that root help exposes clean command tree."""
    result = CliRunner().invoke(main_cli(), ["--help"], terminal_width=220)
    output = unstyle(result.output)
    assert result.exit_code == 0
    for command in ("workspace", "runtime", "db", "rpc", "test", "run", "version"):
        assert re.search(rf"^│ {re.escape(command)}\s", output, re.MULTILINE)
    for command in ("backup", "source", "bootstrap", "launch-import", "deployment-init"):
        assert not re.search(rf"^│ {re.escape(command)}\s", output, re.MULTILINE)


@pytest.mark.parametrize(
    "command",
    [
        ["run", "--help"],
        ["runtime", "launch", "--help"],
        ["runtime", "shell", "--help"],
        ["runtime", "shell-script", "--help"],
        ["db", "restore", "--help"],
        ["test", "load-data", "--help"],
    ],
)
def test_public_commands_do_not_expose_postgresql_ssl_mode(command: list[str]) -> None:
    """The supported sidecar database topology has no gOdoo SSL-mode flag."""
    result = CliRunner().invoke(main_cli(), command, terminal_width=220)

    assert result.exit_code == 0, result.output
    assert "--db-sslmode" not in unstyle(result.output)


def test_test_help_preserves_registered_test_commands():
    """Guards the contract that test help preserves registered test commands."""
    result = CliRunner().invoke(main_cli(), ["test", "--help"], terminal_width=220)
    output = unstyle(result.output)

    assert result.exit_code == 0
    commands = set(re.findall(r"^│ ([\w-]+)\s", output, re.MULTILINE)) - {"--help"}
    assert commands == {"run", "load-data", "get-changed-modules"}


def test_root_version_does_not_require_runtime():
    """Guards the contract that root version does not require runtime."""
    result = CliRunner().invoke(main_cli(), ["version"])
    assert result.exit_code == 0
    assert unstyle(result.output).splitlines() == [f"gOdoo Version: {__about__.__version__}"]


def test_runtime_launch_help_keeps_start_only_contract():
    """Guards the contract that runtime launch help keeps start only contract."""
    result = CliRunner().invoke(main_cli(), ["runtime", "launch", "--help"])
    assert result.exit_code == 0
    assert "without initialization or reconciliation, after additive dependency preflight" in re.sub(
        r"\s+", " ", result.output
    )


def test_workspace_help_exposes_workspace_operations():
    """Guards the contract that workspace help exposes workspace operations."""
    result = CliRunner().invoke(main_cli(), ["workspace", "--help"], terminal_width=220)
    assert result.exit_code == 0
    for command in ("sync", "check", "configure", "storage", "gc"):
        assert command in result.output


@pytest.mark.parametrize(
    "command",
    [
        ["run"],
        ["runtime", "init"],
        ["runtime", "launch"],
        ["db", "prepare"],
        ["test", "run"],
        ["test", "load-data"],
    ],
)
def test_commands_do_not_expose_thirdparty_path(command: list[str]) -> None:
    """Keep third-party source selection inside gOdoo."""
    result = CliRunner().invoke(main_cli(), [*command, "--help"])

    assert result.exit_code == 0
    assert "--thirdparty-addon-path" not in unstyle(result.output)


def test_run_forwards_unknown_odoo_options_through_main_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that run forwards unknown odoo options through main cli."""
    odoo_root = tmp_path / "odoo"
    odoo_root.mkdir()
    (odoo_root / "odoo-bin").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['GODOO_TEST_OUTPUT']).write_text(json.dumps({'argv': sys.argv[1:], 'password': os.environ.get('PGPASSWORD')}))\n"
    )
    for name in ("addons", "thirdparty"):
        (tmp_path / name).mkdir()
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\ndb_password = config-secret\n")
    monkeypatch.setenv("ODOO_MAIN_FOLDER", str(odoo_root))
    monkeypatch.setenv("ODOO_WORKSPACE_ADDON_LOCATION", str(tmp_path / "addons"))
    monkeypatch.setenv("ODOO_CONF_PATH", str(conf))

    for variable in ("ODOO_DB_PASSWORD", "ODOO_DB_USER", "ODOO_MAIN_DB", "ODOO_DB_HOST", "ODOO_DB_PORT"):
        monkeypatch.delenv(variable, raising=False)
    output_path = tmp_path / "child.json"
    monkeypatch.setenv("GODOO_TEST_OUTPUT", str(output_path))
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    with patch("godoo_cli.commands.run.preflight_for_config"):
        result = CliRunner().invoke(
            main_cli(),
            ["run", "--database", "x", "--db-password", "selected-secret", "--stop-after-init"],
        )

    assert result.exit_code == 0, result.output
    child = json.loads(output_path.read_text())
    assert child["password"] == "selected-secret"
    assert "selected-secret" not in child["argv"]
    assert "config-secret" not in child["argv"]
    assert "--config" in child["argv"]


@pytest.mark.parametrize(
    ("arguments", "managed"),
    [
        ([], True),
        (["--stop-after-init"], True),
        (["server"], True),
        (["scaffold", "demo"], False),
        (["db", "list"], False),
        (["module", "list"], False),
        (["--addons-path=/addons", "custom-addon-command"], False),
        (["--addons-path=/addons", "-d", "runtime"], True),
        (["--", "--db_password", "opaque"], True),
    ],
)
def test_public_run_classifies_server_and_native_commands(arguments: list[str], managed: bool) -> None:
    """Server configuration stays confined to server invocations."""
    assert _uses_managed_server_config(arguments) is managed


def test_public_run_preflight_creates_default_project_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards public run's default project environment preflight."""
    odoo_root = tmp_path / "odoo"
    addons = tmp_path / "addons"
    thirdparty = tmp_path / "thirdparty"
    for path in (odoo_root, addons, thirdparty):
        path.mkdir()
    conf = tmp_path / "odoo.conf"
    conf.touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    def dependency_requirements(_config: object, _arguments: object, *, resolved: bool = False) -> list[str]:
        assert resolved is True
        return ["pkg"]

    monkeypatch.setattr("godoo_cli.runtime.odoo.dependency_requirements", dependency_requirements)
    uv_calls: list[list[str]] = []

    def uv_run(command: list[str], **_kwargs: object) -> None:
        uv_calls.append(command)
        if command[:2] == ["uv", "venv"]:
            (tmp_path / ".venv" / "bin").mkdir(parents=True)
            (tmp_path / ".venv" / "bin" / "python").touch()

    monkeypatch.setattr("godoo_cli.runtime.odoo.subprocess.run", uv_run)
    with patch("godoo_cli.commands.run.run_odoo_command", return_value=type("Result", (), {"returncode": 0})()):
        result = run_odoo(
            ["--stop-after-init"],
            odoo_root,
            addons,
            thirdparty,
            conf,
        )

    assert result == 0
    assert uv_calls[0] == ["uv", "venv", str(tmp_path / ".venv")]
    assert str(tmp_path / ".venv" / "bin" / "python") in uv_calls[1]
