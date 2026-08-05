import logging
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from godoo_cli.commands.db.query import DbBootstrapStatus
from godoo_cli.commands.odoo_bin.bootstrap import bootstrap_and_prep_launch_cmd
from godoo_cli.commands.odoo_bin.cli_generate import _boostrap_command, _launch_command
from godoo_cli.models import GodooConfig

LOGGER = logging.getLogger(__name__)


def _assert_option(command: list[str], option: str, value: str) -> None:
    """Assert an option and its value remain separate process arguments."""
    index = command.index(option)
    assert command[index + 1] == value


def _godoo_config(tmp_path: Path, conf_path: Path) -> GodooConfig:
    return GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=conf_path,
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        db_name="godoo_test",
        db_user="odoo_user",
        db_password="secret",
        db_host="postgres",
        db_port=5432,
        db_filter="godoo_test",
        multithread_worker_count=0,
    )


def test_launch_command_saves_missing_config(tmp_path: Path):
    conf_path = tmp_path / "config" / "odoo-test.conf"
    command = _launch_command(
        _godoo_config(tmp_path, conf_path),
        extra_cmd_args=["-u"],
        upgrade_workspace_modules=False,
    )

    _assert_option(command, "--config", str(conf_path))
    assert "--save" in command
    _assert_option(command, "--database", "godoo_test")
    _assert_option(command, "--db_user", "odoo_user")
    _assert_option(command, "--db_password", "secret")
    _assert_option(command, "--db_host", "postgres")
    _assert_option(command, "--db_port", "5432")
    assert "--db-filter=^godoo_test$" in command
    assert conf_path.parent.exists()
    assert not conf_path.exists()


def test_bootstrap_command_uses_shared_config_args(tmp_path: Path):
    conf_path = tmp_path / "config" / "odoo-test.conf"
    command = _boostrap_command(
        _godoo_config(tmp_path, conf_path),
        addon_paths=[],
        install_workspace_modules=False,
    )

    _assert_option(command, "--config", str(conf_path))
    assert "--save" in command
    _assert_option(command, "--database", "godoo_test")
    _assert_option(command, "--db_user", "odoo_user")
    _assert_option(command, "--db_password", "secret")
    _assert_option(command, "--db_host", "postgres")
    _assert_option(command, "--db_port", "5432")
    assert "--db-filter=^godoo_test$" in command
    assert conf_path.parent.exists()


def test_launch_command_does_not_save_existing_config(tmp_path: Path):
    conf_path = tmp_path / "odoo-test.conf"
    conf_path.touch()

    command = _launch_command(
        _godoo_config(tmp_path, conf_path),
        extra_cmd_args=["-u"],
        upgrade_workspace_modules=False,
    )

    assert "--save" not in command
    assert "--database godoo_test" not in command


def test_command_argv_preserves_paths_with_spaces(tmp_path: Path):
    config = _godoo_config(tmp_path, tmp_path / "odoo conf" / "odoo.conf")
    command = _launch_command(
        config,
        extra_cmd_args=["--logfile '/tmp/odoo logs/server.log'"],
        upgrade_workspace_modules=False,
    )

    _assert_option(command, "--config", str(config.odoo_conf_path))
    _assert_option(command, "--logfile", "/tmp/odoo logs/server.log")


def test_command_argv_preserves_already_separated_value_with_spaces(tmp_path: Path):
    config = _godoo_config(tmp_path, tmp_path / "odoo.conf")
    command = _launch_command(
        config,
        extra_cmd_args=["--logfile", "/tmp/odoo logs/server.log"],
        upgrade_workspace_modules=False,
    )

    _assert_option(command, "--logfile", "/tmp/odoo logs/server.log")


def test_addon_paths_are_stable_and_allow_missing_thirdparty_custom(tmp_path: Path):
    config = _godoo_config(tmp_path, tmp_path / "odoo.conf")
    (config.odoo_install_folder / "addons").mkdir(parents=True)
    (config.odoo_install_folder / "odoo" / "addons").mkdir(parents=True)
    for repository, module in (
        (config.workspace_addon_path, "workspace_module"),
        (config.thirdparty_addon_path / "z_repo", "z_module"),
        (config.thirdparty_addon_path / "a_repo", "a_module"),
    ):
        (repository / module).mkdir(parents=True)
        (repository / module / "__manifest__.py").write_text("{}")

    assert config.addon_paths == [
        config.odoo_install_folder / "addons",
        config.odoo_install_folder / "odoo" / "addons",
        config.workspace_addon_path,
        config.thirdparty_addon_path / "a_repo",
        config.thirdparty_addon_path / "z_repo",
    ]


def test_prep_launch_skips_update_for_missing_config(tmp_path: Path):
    conf_path = tmp_path / "odoo-test.conf"
    godoo_conf = cast(
        GodooConfig,
        SimpleNamespace(
            db_connection=SimpleNamespace(cli_dict={}),
            db_name="godoo_test",
            db_user="odoo",
            db_host="postgres",
            db_port=5432,
            db_password="secret",
            db_filter="godoo_test",
            odoo_install_folder=tmp_path / "odoo",
            odoo_conf_path=conf_path,
            workspace_addon_path=tmp_path / "addons",
            thirdparty_addon_path=tmp_path / "thirdparty",
            data_dir=tmp_path / "data",
            multithread_worker_count=0,
            languages="en_US",
            odoo_version=SimpleNamespace(major=16),
        ),
    )

    with (
        patch(
            "godoo_cli.commands.odoo_bin.bootstrap._is_bootstrapped",
            return_value=DbBootstrapStatus.BOOTSTRAPPED,
        ),
        patch("godoo_cli.commands.odoo_bin.bootstrap.update_odoo_conf") as update_conf,
        patch("godoo_cli.commands.odoo_bin.bootstrap.py_depends_by_db"),
        patch(
            "godoo_cli.commands.odoo_bin.bootstrap._launch_command",
            return_value="odoo command",
        ),
    ):
        command = bootstrap_and_prep_launch_cmd(
            godoo_conf=godoo_conf,
            odoo_demo=False,
            dev_mode=False,
        )

    assert command == "odoo command"
    update_conf.assert_not_called()
