"""Tests Odoo launch and shell command boundary behavior."""

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.commands.runtime.launch import launch_odoo
from godoo_cli.commands.runtime.shell import (
    odoo_shell,
    odoo_shell_run_script,
)
from godoo_cli.commands.test.load_data import odoo_load_test_data
from godoo_cli.database.state import DbBootstrapStatus
from godoo_cli.runtime.odoo import execution_python, odoo_command_argv, preflight_for_config, run_odoo_command
from godoo_cli.workspace.types import ResolvedSource, WorkspaceSettings


@pytest.fixture(autouse=True)
def skip_dependency_installation_for_command_shape_tests(monkeypatch: pytest.MonkeyPatch):
    """Keep command construction tests independent of a live PostgreSQL server."""
    monkeypatch.setattr("godoo_cli.commands.runtime.launch.preflight_for_config", lambda *_args: None)
    monkeypatch.setattr("godoo_cli.runtime.odoo.preflight_for_config", lambda *_args: None)
    monkeypatch.setattr("godoo_cli.runtime.odoo.require_odoo_version", lambda *_args: None)


LOGGER = logging.getLogger(__name__)


class CommandPaths(TypedDict):
    odoo_main_path: Path
    workspace_addon_path: Path
    odoo_conf_path: Path


def _command_paths(tmp_path: Path) -> CommandPaths:
    odoo_path = tmp_path / "odoo"
    workspace_path = tmp_path / "workspace"
    thirdparty_path = tmp_path / "thirdparty"
    for path in (odoo_path, workspace_path, thirdparty_path, thirdparty_path / "custom"):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "odoo_main_path": odoo_path,
        "workspace_addon_path": workspace_path,
        "odoo_conf_path": tmp_path / "odoo.conf",
    }


def _development_sources(tmp_path: Path) -> tuple[WorkspaceSettings, list[ResolvedSource], list[dict[str, str]]]:
    """Return a selected Odoo worktree without mutating source management."""
    selected = tmp_path / "selected-odoo"
    selected.mkdir()
    source = ResolvedSource(
        role="odoo",
        prefix="",
        name="odoo",
        url="https://example.test/odoo.git",
        branch="19.0",
        worktree_branch="godoo/19.0",
        requested_commit="",
        base_commit="base",
        resolved_commit="resolved",
        merge_from=(),
        merge_commits=(),
        recipe_fingerprint="recipe",
        host_path=selected,
        container_path=Path("/odoo/odoo"),
    )
    settings = WorkspaceSettings(tmp_path, tmp_path / "odoo_manifest.yml", tmp_path / "sources")
    return settings, [source], []


def test_launch_validates_manifest_selected_odoo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Launch resolves the selected development Odoo root before validation."""
    paths = _command_paths(tmp_path)
    settings, sources, archives = _development_sources(tmp_path)
    observed: list[Path] = []
    monkeypatch.setattr(
        "godoo_cli.commands.configuration._development_sources",
        lambda: (settings, sources, archives),
    )
    monkeypatch.setattr(
        "godoo_cli.commands.runtime.launch.require_cli_odoo_version",
        lambda path, _specifier: observed.append(path),
    )
    monkeypatch.setattr(
        "godoo_cli.commands.runtime.launch.build_launch_command",
        lambda *_args, **_kwargs: ["odoo-bin", "server"],
    )
    monkeypatch.setattr(
        "godoo_cli.commands.runtime.launch.run_odoo_command",
        lambda _command: SimpleNamespace(returncode=0),
    )

    assert launch_odoo(**paths, db_filter="runtime", db_name="runtime", db_user="odoo") == 0
    assert observed == [sources[0].host_path]


def test_launch_uses_configured_data_dir_when_option_is_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Launch leaves data-dir precedence to the selected Odoo config file."""
    paths = _command_paths(tmp_path)
    configured_data_dir = tmp_path / "configured-data"
    paths["odoo_conf_path"].write_text(f"[options]\ndata_dir = {configured_data_dir}\n", encoding="utf-8")
    observed: list[Path] = []
    monkeypatch.setattr("godoo_cli.commands.runtime.launch.require_cli_odoo_version", lambda *_args: None)
    monkeypatch.setattr(
        "godoo_cli.commands.runtime.launch.build_launch_command",
        lambda config, *_args, **_kwargs: observed.append(config.data_dir) or ["odoo-bin", "server"],
    )
    monkeypatch.setattr(
        "godoo_cli.commands.runtime.launch.run_odoo_command",
        lambda _command: SimpleNamespace(returncode=0),
    )

    assert launch_odoo(**paths, db_filter="runtime", db_name="runtime", db_user="odoo") == 0
    assert observed == [configured_data_dir]


def test_launch_is_non_destructive_and_does_not_prepare_or_upgrade_workspace(tmp_path: Path):
    """Guards the contract that launch is non destructive and does not prepare or upgrade workspace."""
    paths = _command_paths(tmp_path)
    with (
        patch("godoo_cli.commands.runtime.launch.require_cli_odoo_version"),
        patch("godoo_cli.commands.runtime.launch.build_launch_command", return_value=["odoo-bin", "server"]) as build,
        patch(
            "godoo_cli.commands.runtime.launch.run_odoo_command",
            return_value=SimpleNamespace(returncode=0),
        ) as run_command,
    ):
        launch_odoo(
            **paths,
            db_filter="runtime",
            db_name="runtime",
            db_user="odoo",
            data_dir=tmp_path / "data",
        )

    assert build.call_args.kwargs["upgrade_workspace_modules"] is False
    run_command.assert_called_once_with(["odoo-bin", "server"])


def test_launch_dev_mode_is_environment_backed():
    """Guards the contract that launch dev mode is environment backed."""
    app = typer.Typer()
    app.command()(launch_odoo)

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "GODOO_DEV_MODE" in result.output


def test_launch_wraps_odoo_with_debugpy_without_preparation(tmp_path: Path):
    """Guards the contract that launch wraps odoo with debugpy without preparation."""
    paths = _command_paths(tmp_path)
    with (
        patch("godoo_cli.commands.runtime.launch.require_cli_odoo_version"),
        patch("godoo_cli.commands.runtime.launch.build_launch_command", return_value=["/odoo/odoo-bin", "server"]),
        patch(
            "godoo_cli.commands.runtime.launch.run_odoo_command",
            return_value=SimpleNamespace(returncode=0),
        ) as run_command,
    ):
        launch_odoo(
            **paths,
            db_filter="runtime",
            db_name="runtime",
            db_user="odoo",
            debug_listen="0.0.0.0:5678",
            debug_wait_for_client=True,
            multithread_worker_count=0,
        )

    command = run_command.call_args.args[0]
    assert command[1:6] == ["-m", "debugpy", "--listen", "0.0.0.0:5678", "--wait-for-client"]
    assert command[-2:] == ["/odoo/odoo-bin", "server"]


def test_launch_rejects_debug_wait_without_listener(tmp_path: Path):
    """Guards the contract that launch rejects debug wait without listener."""
    paths = _command_paths(tmp_path)
    with (
        patch("godoo_cli.commands.runtime.launch.require_cli_odoo_version"),
        pytest.raises(typer.BadParameter, match="requires --debug-listen"),
    ):
        launch_odoo(
            **paths,
            db_filter="runtime",
            db_name="runtime",
            db_user="odoo",
            debug_wait_for_client=True,
        )


def test_parent_debugger_disables_reload_and_avoids_a_second_listener(tmp_path: Path):
    """Guards the contract that parent debugger disables reload and avoids a second listener."""
    paths = _command_paths(tmp_path)
    with (
        patch("godoo_cli.commands.runtime.launch.require_cli_odoo_version"),
        patch("godoo_cli.commands.runtime.launch.odoo_debugger_attached", return_value=True),
        patch(
            "godoo_cli.commands.runtime.launch.build_launch_command",
            return_value=["/odoo/odoo-bin", "server"],
        ) as build,
        patch("godoo_cli.commands.runtime.launch.run_odoo_command", return_value=SimpleNamespace(returncode=0)) as run,
    ):
        assert (
            launch_odoo(
                **paths,
                db_filter="runtime",
                db_name="runtime",
                db_user="odoo",
                dev_mode=True,
                debug_listen="0.0.0.0:5678",
                debug_wait_for_client=True,
            )
            == 0
        )
    assert build.call_args.args[1] == ["--dev", "xml,qweb"]
    run.assert_called_once_with(["/odoo/odoo-bin", "server"])


def test_parent_debugger_launches_odoo_through_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that parent debugger launches odoo through python."""
    executable = tmp_path / "odoo-bin"
    executable.write_text("print('instrumentable Odoo child')\n")
    monkeypatch.setitem(sys.modules, "debugpy", SimpleNamespace(is_client_connected=lambda: True))

    result = run_odoo_command([str(executable)], capture_output=True, text=True)

    assert result.args == [str(execution_python()), str(executable)]
    assert result.returncode == 0
    assert result.stdout.strip() == "instrumentable Odoo child"


def test_shell_passes_addon_paths_when_config_is_missing(tmp_path: Path):
    """Guards the contract that shell passes addon paths when config is missing."""
    paths = _command_paths(tmp_path)
    addon_path = tmp_path / "custom-addons"
    addon_path.mkdir()
    with (
        patch("godoo_cli.commands.runtime.shell.require_cli_odoo_version"),
        patch(
            "godoo_cli.commands.runtime.shell.run_odoo_command",
            return_value=SimpleNamespace(returncode=0),
        ) as run_command,
    ):
        odoo_shell(
            odoo_main_path=paths["odoo_main_path"],
            odoo_conf_path=paths["odoo_conf_path"],
            db_name="runtime",
            db_user="odoo",
            db_password="secret",
            db_host="postgres",
            db_port=5432,
            data_dir=tmp_path / "data",
            addon_paths=[addon_path],
            pipe_in_command="env['x']",
        )

    command = run_command.call_args.args[0]
    assert command[command.index("--addons-path") + 1] == str(addon_path.absolute())


def test_shell_forwards_explicit_empty_socket_overrides(tmp_path: Path):
    """Guards that explicit local-socket overrides reach the Odoo child."""
    paths = _command_paths(tmp_path)
    with (
        patch("godoo_cli.commands.runtime.shell.require_cli_odoo_version"),
        patch(
            "godoo_cli.commands.runtime.shell.run_odoo_command",
            return_value=SimpleNamespace(returncode=0),
        ) as run_command,
    ):
        odoo_shell(
            odoo_main_path=paths["odoo_main_path"],
            odoo_conf_path=paths["odoo_conf_path"],
            db_name="runtime",
            db_user="odoo",
            pipe_in_command="env['x']",
        )

    command = run_command.call_args.args[0]
    assert command[command.index("--database") + 1] == "runtime"
    assert command[command.index("--db_user") + 1] == "odoo"
    assert command[command.index("--db_host") + 1] == ""
    assert command[command.index("--db_port") + 1] == "0"
    assert command[command.index("--db_password") + 1] == ""


def test_shell_overrides_stale_config_database_settings(tmp_path: Path):
    """Guards the contract that shell overrides stale config database settings."""
    paths = _command_paths(tmp_path)
    paths["odoo_conf_path"].touch()
    with (
        patch("godoo_cli.commands.runtime.shell.require_cli_odoo_version"),
        patch(
            "godoo_cli.commands.runtime.shell.run_odoo_command",
            return_value=SimpleNamespace(returncode=0),
        ) as run_command,
    ):
        odoo_shell(
            odoo_main_path=paths["odoo_main_path"],
            odoo_conf_path=paths["odoo_conf_path"],
            db_name="selected-runtime",
            db_user="selected-user",
            db_password="selected-secret",
            db_host="selected-host",
            db_port=5544,
            pipe_in_command="env['x']",
        )

    command = run_command.call_args.args[0]
    assert command[command.index("--database") + 1] == "selected-runtime"
    assert command[command.index("--db_user") + 1] == "selected-user"
    assert command[command.index("--db_host") + 1] == "selected-host"
    assert command[command.index("--db_port") + 1] == "5544"


def test_shell_script_injects_arguments_and_uses_the_shared_shell_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Guards the contract that shell script injects arguments and uses the shared shell preflight."""
    paths = _command_paths(tmp_path)
    scripts = tmp_path / "shell_scripts"
    scripts.mkdir()
    (scripts / "probe.py").write_text("result = script_args\n")
    monkeypatch.setattr("godoo_cli.commands.runtime.shell.SHELL_SCRIPTS_PATH", scripts)

    with (
        patch("godoo_cli.commands.runtime.shell.require_cli_odoo_version"),
        patch(
            "godoo_cli.commands.runtime.shell.run_odoo_command",
            return_value=SimpleNamespace(returncode=0),
        ) as run_command,
    ):
        odoo_shell_run_script(
            "probe",
            odoo_main_path=paths["odoo_main_path"],
            odoo_conf_path=paths["odoo_conf_path"],
            db_name="runtime",
            db_user="odoo",
            script_args=["first", "second"],
            addon_paths=[paths["workspace_addon_path"]],
        )

    assert run_command.call_args.kwargs["input"] == "script_args = ['first', 'second']\n\nresult = script_args\n"


def test_load_test_data_shell_uses_effective_direct_database_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The public load-data command passes direct DB overrides to its shell consumer."""
    odoo = tmp_path / "odoo"
    workspace = tmp_path / "workspace"
    module = workspace / "sale"
    (module / "tests").mkdir(parents=True)
    (module / "tests" / "data.py").write_text("")
    conf = tmp_path / "stale.conf"
    conf.write_text("[options]\ndb_name=stale\ndb_user=stale-user\ndb_host=stale-host\ndb_port=5432\n")
    fake_module = SimpleNamespace(name="sale", path=module)
    observed = []
    monkeypatch.setattr("godoo_cli.commands.test.load_data.require_cli_odoo_version", lambda *_: None)
    monkeypatch.setattr(
        "godoo_cli.commands.test.load_data.GodooModules",
        lambda *_: SimpleNamespace(get_modules=lambda *_: [fake_module]),
    )
    monkeypatch.setattr("godoo_cli.commands.test.load_data.bootstrap_and_prep_launch_cmd", lambda **_: 0)
    monkeypatch.setattr("godoo_cli.commands.test.load_data.odoo_shell", lambda **kwargs: observed.append(kwargs) or 0)
    odoo_load_test_data(
        test_modules=["sale"],
        odoo_main_path=odoo,
        workspace_addon_path=workspace,
        odoo_conf_path=conf,
        db_filter="",
        db_name="direct-db",
        db_user="direct-user",
        data_dir=tmp_path / "data",
        db_host="direct-host",
        db_port=5544,
        db_password="direct-secret",
    )
    assert observed[0]["db_name"] == "direct-db"
    assert observed[0]["db_user"] == "direct-user"
    assert observed[0]["db_host"] == "direct-host"
    assert observed[0]["db_port"] == 5544
    assert observed[0]["db_password"] == "direct-secret"
    assert "db_sslmode" not in observed[0]


def test_odoo_command_argv_parses_legacy_command_strings():
    """Guards the contract that odoo command argv parses legacy command strings."""
    assert odoo_command_argv("odoo-bin server --config '/tmp/odoo conf'") == [
        "odoo-bin",
        "server",
        "--config",
        "/tmp/odoo conf",
    ]


def test_run_odoo_command_forwards_stdin_without_a_shell():
    """Guards the contract that run odoo command forwards stdin without a shell."""
    import sys

    result = run_odoo_command(
        [sys.executable, "-c", "import sys; print(sys.stdin.read()); sys.exit(7)"],
        input="env['res.users']",
        text=True,
        capture_output=True,
    )

    assert result.returncode == 7
    assert result.stdout == "env['res.users']\n"


def test_shell_major_guard_uses_the_effective_cli_selected_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _command_paths(tmp_path)
    selected_names: list[str] = []
    monkeypatch.setattr("godoo_cli.commands.runtime.shell.require_cli_odoo_version", lambda *_args: None)
    monkeypatch.setattr("godoo_cli.runtime.odoo.preflight_for_config", preflight_for_config)
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.require_supported_odoo_runtime",
        lambda _path: SimpleNamespace(major=19),
    )
    monkeypatch.setattr(
        "godoo_cli.runtime.odoo.classify_bootstrap_state",
        lambda connection: selected_names.append(connection.db_name) or DbBootstrapStatus.BOOTSTRAPPED,
    )
    monkeypatch.setattr("godoo_cli.runtime.odoo.base_module_major", lambda _connection: 18)

    with pytest.raises(RuntimeError, match=r"selected-runtime.*contains Odoo 18"):
        odoo_shell(
            odoo_main_path=paths["odoo_main_path"],
            odoo_conf_path=paths["odoo_conf_path"],
            db_name="selected-runtime",
            db_user="odoo",
            data_dir=tmp_path / "data",
        )

    assert selected_names == ["selected-runtime"]
