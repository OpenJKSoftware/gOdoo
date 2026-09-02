from configparser import ConfigParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer
from click import unstyle
from typer.testing import CliRunner

from godoo_cli.commands.odoo_bin.godoo_test.run import odoo_run_tests
from godoo_cli.commands.odoo_bin.launch import launch_import, launch_odoo, prepare_odoo
from godoo_cli.commands.odoo_bin.shell import odoo_shell
from godoo_cli.helpers.odoo_command import odoo_command_argv, run_odoo_command


def _command_paths(tmp_path: Path) -> dict[str, Path]:
    odoo_path = tmp_path / "odoo"
    workspace_path = tmp_path / "workspace"
    thirdparty_path = tmp_path / "thirdparty"
    for path in (odoo_path, workspace_path, thirdparty_path, thirdparty_path / "custom"):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "odoo_main_path": odoo_path,
        "workspace_addon_path": workspace_path,
        "thirdparty_addon_path": thirdparty_path,
        "odoo_conf_path": tmp_path / "odoo.conf",
    }


def test_launch_is_non_destructive_and_does_not_prepare_or_upgrade_workspace(tmp_path: Path):
    paths = _command_paths(tmp_path)
    with (
        patch("godoo_cli.commands.odoo_bin.launch.require_odoo_version"),
        patch("godoo_cli.commands.odoo_bin.launch._launch_command", return_value=["odoo-bin", "server"]) as build,
        patch(
            "godoo_cli.commands.odoo_bin.launch.run_odoo_command",
            return_value=SimpleNamespace(returncode=0),
        ) as run_command,
    ):
        result = launch_odoo(
            **paths,
            db_filter="runtime",
            db_name="runtime",
            db_user="odoo",
            data_dir=tmp_path / "data",
            install_workspace_modules=False,
        )

    assert result == 0
    assert build.call_args.kwargs["upgrade_workspace_modules"] is False
    run_command.assert_called_once_with(["odoo-bin", "server"])


def test_launch_dev_mode_is_environment_backed():
    app = typer.Typer()
    app.command()(launch_odoo)

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "GODOO_DEV_MODE" in result.output


def test_prepare_updates_dependencies_without_launching(tmp_path: Path):
    paths = _command_paths(tmp_path)
    with (
        patch("godoo_cli.commands.odoo_bin.launch.require_odoo_version"),
        patch("godoo_cli.commands.odoo_bin.launch.update_odoo_conf") as update_conf,
        patch("godoo_cli.commands.odoo_bin.launch.install_base_python_reqs") as install_base,
        patch("godoo_cli.commands.odoo_bin.launch.install_py_reqs_for_modules") as install_modules,
    ):
        result = prepare_odoo(**paths, data_dir=tmp_path / "data")

    assert result is None
    update_conf.assert_not_called()
    install_base.assert_called_once_with(paths["odoo_main_path"])
    install_modules.assert_called_once()
    assert paths["odoo_conf_path"].is_file()


def test_prepare_synchronizes_sources_before_runtime_preparation(tmp_path: Path):
    paths = _command_paths(tmp_path)
    manifest = tmp_path / "odoo_manifest.yml"
    archives = tmp_path / "archives"
    calls: list[str] = []

    with (
        patch(
            "godoo_cli.commands.odoo_bin.launch.sync_source", side_effect=lambda *_args, **_kwargs: calls.append("sync")
        ) as synchronize,
        patch(
            "godoo_cli.commands.odoo_bin.launch.prepare_runtime",
            side_effect=lambda *_args, **_kwargs: calls.append("prepare"),
        ),
    ):
        prepare_odoo(
            **paths,
            data_dir=tmp_path / "data",
            sync_sources=True,
            manifest_path=manifest,
            thirdparty_zip_source=archives,
        )

    synchronize.assert_called_once()
    assert synchronize.call_args.kwargs["manifest_path"] == manifest
    assert synchronize.call_args.kwargs["thirdparty_zip_source"] == archives
    assert synchronize.call_args.kwargs["remove_unspecified_addons"] is True
    assert calls == ["sync", "prepare"]


def test_prepare_rejects_incomplete_source_sync_configuration(tmp_path: Path):
    with pytest.raises(typer.BadParameter, match="requires ODOO_MANIFEST"):
        prepare_odoo(
            **_command_paths(tmp_path),
            data_dir=tmp_path / "data",
            sync_sources=True,
        )


def test_prepare_persists_explicit_x_sendfile_policy(tmp_path: Path):
    paths = _command_paths(tmp_path)
    with (
        patch("godoo_cli.commands.odoo_bin.launch.require_odoo_version"),
        patch("godoo_cli.commands.odoo_bin.launch.install_base_python_reqs"),
        patch("godoo_cli.commands.odoo_bin.launch.install_py_reqs_for_modules"),
    ):
        prepare_odoo(
            **paths,
            data_dir=tmp_path / "data",
            db_filter="runtime",
            db_name="runtime",
            db_user="odoo",
            x_sendfile=True,
        )

    parser = ConfigParser()
    parser.read(paths["odoo_conf_path"])
    assert parser["options"]["x_sendfile"] == "True"
    assert parser["options"]["db_name"] == "runtime"
    assert parser["options"]["dbfilter"] == "^runtime$"


def test_shell_passes_addon_paths_when_config_is_missing(tmp_path: Path):
    paths = _command_paths(tmp_path)
    addon_path = tmp_path / "custom-addons"
    addon_path.mkdir()
    with (
        patch("godoo_cli.commands.odoo_bin.shell.require_odoo_version"),
        patch(
            "godoo_cli.commands.odoo_bin.shell.run_odoo_command",
            return_value=SimpleNamespace(returncode=0),
        ) as run_command,
    ):
        result = odoo_shell(
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

    assert result == 0
    command = run_command.call_args.args[0]
    assert command[command.index("--addons-path") + 1] == str(addon_path.absolute())


def test_shell_supports_local_socket_auth_without_optional_connection_flags(tmp_path: Path):
    paths = _command_paths(tmp_path)
    with (
        patch("godoo_cli.commands.odoo_bin.shell.require_odoo_version"),
        patch(
            "godoo_cli.commands.odoo_bin.shell.run_odoo_command",
            return_value=SimpleNamespace(returncode=0),
        ) as run_command,
    ):
        result = odoo_shell(
            odoo_main_path=paths["odoo_main_path"],
            odoo_conf_path=paths["odoo_conf_path"],
            db_name="runtime",
            db_user="odoo",
            db_host="",
            db_port=0,
            db_password="",
            pipe_in_command="env['x']",
        )

    assert result == 0
    command = run_command.call_args.args[0]
    assert "--database=runtime" in command
    assert "--db_user=odoo" in command
    assert not any(argument.startswith(("--db_host", "--db_port", "--db_password")) for argument in command)


def test_shell_addon_path_option_is_repeatable_and_env_backed():
    app = typer.Typer()
    app.command()(odoo_shell)
    result = CliRunner().invoke(app, ["--help"])
    output = unstyle(result.output)

    assert result.exit_code == 0
    assert "--addon-path" in output
    assert "ODOO_ADDON_PATHS" in output


def test_launch_import_disables_odoo_reload_mode(tmp_path: Path):
    paths = _command_paths(tmp_path)
    with (
        patch("godoo_cli.commands.odoo_bin.launch.require_odoo_version"),
        patch(
            "godoo_cli.commands.odoo_bin.launch.bootstrap_and_prep_launch_cmd",
            return_value=["odoo-bin", "--dev", "xml,qweb,reload"],
        ),
        patch("godoo_cli.commands.odoo_bin.launch.threading.Thread") as thread,
        patch(
            "godoo_cli.commands.odoo_bin.launch.run_odoo_command",
            return_value=SimpleNamespace(returncode=0),
        ) as run_command,
    ):
        result = launch_import(
            [tmp_path / "data.json"],
            **paths,
            db_filter="runtime",
            db_name="runtime",
            db_user="odoo",
            rpc_host="localhost",
            rpc_user="admin",
            rpc_password="admin",
            odoo_demo=False,
            dev_mode=True,
        )

    assert result == 0
    thread.return_value.start.assert_called_once()
    run_command.assert_called_once_with(["odoo-bin", "--dev", "xml,qweb"])


def test_test_runner_passes_single_threaded_odoo_arguments(tmp_path: Path):
    paths = _command_paths(tmp_path)
    module = SimpleNamespace(name="sale")
    module_registry = MagicMock()
    module_registry.get_modules.return_value = [module]
    module_registry.get_module_dependencies.return_value = []

    with (
        patch("godoo_cli.commands.odoo_bin.godoo_test.run.require_odoo_version"),
        patch(
            "godoo_cli.commands.odoo_bin.godoo_test.run.GodooModules",
            return_value=module_registry,
        ),
        patch(
            "godoo_cli.commands.odoo_bin.godoo_test.run.bootstrap_and_prep_launch_cmd",
            return_value=["odoo-bin", "server", "--test-tags", "/sale"],
        ) as prepare_launch,
        patch(
            "godoo_cli.commands.odoo_bin.godoo_test.run.run_odoo_command",
            return_value=SimpleNamespace(returncode=0),
        ) as run_command,
    ):
        result = odoo_run_tests(
            ["sale"],
            **paths,
            db_filter="test_db",
            db_user="odoo",
            db_name="test_db",
            data_dir=tmp_path / "data",
            odoo_log_level="warning",
        )

    assert result == 0
    prepare_kwargs = prepare_launch.call_args.kwargs
    assert prepare_kwargs["godoo_conf"].multithread_worker_count == 0
    assert prepare_kwargs["godoo_conf"].data_dir == tmp_path / "data"
    assert prepare_kwargs["extra_launch_args"] == [
        "-u sale",
        "--log-level warning",
        "--test-tags /sale",
        "--stop-after-init",
    ]
    run_command.assert_called_once_with(["odoo-bin", "server", "--test-tags", "/sale"])


def test_odoo_command_argv_parses_legacy_command_strings():
    assert odoo_command_argv("odoo-bin server --config '/tmp/odoo conf'") == [
        "odoo-bin",
        "server",
        "--config",
        "/tmp/odoo conf",
    ]


def test_run_odoo_command_forwards_stdin_without_a_shell():
    with patch("godoo_cli.helpers.odoo_command.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
        result = run_odoo_command(["odoo-bin", "shell"], input="env['res.users']", text=True)

    assert result.returncode == 0
    run.assert_called_once_with(
        ["odoo-bin", "shell"],
        check=False,
        input="env['res.users']",
        text=True,
    )
