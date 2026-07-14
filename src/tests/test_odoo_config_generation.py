import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from godoo_cli.commands.db.query import DbBootstrapStatus
from godoo_cli.commands.odoo_bin.bootstrap import bootstrap_and_prep_launch_cmd
from godoo_cli.commands.odoo_bin.cli_generate import _boostrap_command, _launch_command
from godoo_cli.models import GodooConfig

LOGGER = logging.getLogger(__name__)


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
    )


def test_launch_command_saves_missing_config(tmp_path: Path):
    conf_path = tmp_path / "config" / "odoo-test.conf"
    command = _launch_command(
        _godoo_config(tmp_path, conf_path),
        extra_cmd_args=["-u"],
        upgrade_workspace_modules=False,
    )

    assert f"--config {conf_path}" in command
    assert "--save" in command
    assert "--database godoo_test" in command
    assert "--db_user odoo_user" in command
    assert "--db_password secret" in command
    assert "--db_host postgres" in command
    assert "--db_port 5432" in command
    assert "--db-filter=^godoo_test$" in command
    assert conf_path.parent.exists()
    assert not conf_path.exists()


def test_bootstrap_command_uses_shared_config_args(tmp_path: Path):
    conf_path = tmp_path / "config" / "odoo-test.conf"
    godoo_config = _godoo_config(tmp_path, conf_path)
    godoo_config.multithread_worker_count = 0

    command = _boostrap_command(
        godoo_config,
        addon_paths=[],
        install_workspace_modules=False,
    )

    assert f"--config {conf_path}" in command
    assert "--save" in command
    assert "--database godoo_test" in command
    assert "--db_user odoo_user" in command
    assert "--db_password secret" in command
    assert "--db_host postgres" in command
    assert "--db_port 5432" in command
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


def test_prep_launch_skips_update_for_missing_config(tmp_path: Path):
    conf_path = tmp_path / "odoo-test.conf"
    godoo_conf = SimpleNamespace(
        db_connection=SimpleNamespace(cli_dict={}),
        db_name="godoo_test",
        db_filter="godoo_test",
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=conf_path,
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        multithread_worker_count=0,
        languages="en_US",
        odoo_version=SimpleNamespace(major=16),
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
