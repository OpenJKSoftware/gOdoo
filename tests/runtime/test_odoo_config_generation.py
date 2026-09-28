"""Tests generated Odoo configuration and command argument contracts."""

import configparser
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from godoo_cli.database.state import DbBootstrapStatus
from godoo_cli.models import GodooConfig
from godoo_cli.runtime.odoo import (
    bootstrap_and_prep_launch_cmd,
    build_bootstrap_command,
    build_launch_command,
    prepare_runtime,
)


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
    """Guards the contract that launch command saves missing config."""
    conf_path = tmp_path / "config" / "odoo-test.conf"
    command = build_launch_command(
        _godoo_config(tmp_path, conf_path),
        extra_cmd_args=["-u"],
        upgrade_workspace_modules=False,
    )

    _assert_option(command, "--config", str(conf_path))
    assert "--save" in command
    _assert_option(command, "--database", "godoo_test")
    _assert_option(command, "--db_user", "odoo_user")
    _assert_option(command, "--db_host", "postgres")
    _assert_option(command, "--db_port", "5432")
    assert "--db-filter=^godoo_test$" in command
    assert conf_path.parent.exists()
    assert not conf_path.exists()


def test_bootstrap_command_uses_shared_config_args(tmp_path: Path):
    """Guards the contract that bootstrap command uses shared config args."""
    conf_path = tmp_path / "config" / "odoo-test.conf"
    command = build_bootstrap_command(
        _godoo_config(tmp_path, conf_path),
        addon_paths=[],
        install_workspace_modules=False,
    )

    _assert_option(command, "--config", str(conf_path))
    assert "--save" not in command
    assert command[-1] == "--no-http"
    _assert_option(command, "--database", "godoo_test")
    _assert_option(command, "--db_user", "odoo_user")
    _assert_option(command, "--db_host", "postgres")
    _assert_option(command, "--db_port", "5432")
    assert "--db-filter=^godoo_test$" in command
    assert not conf_path.parent.exists()


@pytest.mark.parametrize("unsafe_arg", ["--save", "-s", "--test-enable", "--test-tags=standard", "--test-file", "-t"])
def test_bootstrap_rejects_options_that_could_enable_http_or_save_it(tmp_path: Path, unsafe_arg: str):
    """Keep initialization HTTP-free without persisting that setting."""
    with pytest.raises(ValueError, match="no-HTTP"):
        build_bootstrap_command(
            _godoo_config(tmp_path, tmp_path / "odoo.conf"),
            addon_paths=[],
            extra_cmd_args=[unsafe_arg],
            install_workspace_modules=False,
        )


def test_launch_applies_production_workers_before_dev_override(tmp_path: Path):
    """The app selects workers independently of initialization."""
    config = replace(_godoo_config(tmp_path, tmp_path / "odoo.conf"), multithread_worker_count=2)
    command = build_launch_command(config, ["--workers=0"], upgrade_workspace_modules=False)
    _assert_option(command, "--workers", "2")
    assert "--proxy-mode" in command
    assert command[-1] == "--workers=0"


def test_launch_command_does_not_save_existing_config(tmp_path: Path):
    """Guards the contract that launch command does not save existing config."""
    conf_path = tmp_path / "odoo-test.conf"
    conf_path.touch()

    command = build_launch_command(
        _godoo_config(tmp_path, conf_path),
        extra_cmd_args=["-u"],
        upgrade_workspace_modules=False,
    )

    assert "--save" not in command
    _assert_option(command, "--database", "godoo_test")
    _assert_option(command, "--db_user", "odoo_user")
    _assert_option(command, "--db_host", "postgres")
    _assert_option(command, "--db_port", "5432")


def test_addon_paths_are_stable_and_allow_missing_thirdparty_custom(tmp_path: Path):
    """Guards the contract that addon paths are stable and allow missing thirdparty custom."""
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


def test_test_runtime_wrapper_keeps_preflight_before_launch(tmp_path: Path):
    """Guards the contract that test runtime wrapper keeps preflight before launch."""
    config = _godoo_config(tmp_path, tmp_path / "odoo.conf")
    with (
        patch(
            "godoo_cli.database.state.classify_bootstrap_state",
            return_value=DbBootstrapStatus.BOOTSTRAPPED,
        ),
        patch("godoo_cli.runtime.odoo.preflight_for_config") as preflight,
    ):
        command = bootstrap_and_prep_launch_cmd(
            config,
            odoo_demo=False,
            dev_mode=False,
            extra_launch_args=["--update", "sale"],
            install_workspace_addons=False,
        )

    assert isinstance(command, list)
    assert command[command.index("--update") + 1] == "sale"
    assert preflight.call_args.args[1] == command[1:]


def test_prepare_runtime_persists_explicit_empty_database_values(tmp_path: Path):
    """An explicit local-socket configuration remains representable on disk."""
    config = replace(
        _godoo_config(tmp_path, tmp_path / "odoo.conf"),
        db_name="",
        db_user="",
        db_password="",
        db_host="",
        db_port=0,
        db_filter="",
        db_sslmode="require",
    )
    with patch("godoo_cli.runtime.odoo.require_odoo_version"):
        prepare_runtime(config)

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config.odoo_conf_path, encoding="utf-8")
    options = parser["options"]
    assert {
        key: options[key]
        for key in (
            "db_name",
            "db_user",
            "db_password",
            "db_host",
            "db_port",
            "dbfilter",
            "db_sslmode",
            "http_interface",
            "http_enable",
        )
    } == {
        "db_name": "",
        "db_user": "",
        "db_password": "",
        "db_host": "",
        "db_port": "0",
        "dbfilter": "",
        "db_sslmode": "require",
        "http_interface": "0.0.0.0",
        "http_enable": "True",
    }
