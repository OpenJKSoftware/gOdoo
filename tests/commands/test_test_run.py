"""Test the configuration boundary of the Odoo test command."""

import configparser
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer import BadParameter
from typer.testing import CliRunner

from godoo_cli.commands.test import run as test_run
from godoo_cli.commands.test.cli import test_cli_app
from godoo_cli.models import GodooConfig


def _config(tmp_path: Path) -> GodooConfig:
    odoo_path = tmp_path / "odoo"
    workspace_path = tmp_path / "workspace"
    thirdparty_path = tmp_path / "thirdparty"
    for path in (odoo_path, workspace_path, thirdparty_path):
        path.mkdir()
    return GodooConfig(
        odoo_install_folder=odoo_path,
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=workspace_path,
        thirdparty_addon_path=thirdparty_path,
        db_name="selected_test_db",
        db_filter="selected_test_db",
    )


@pytest.mark.parametrize(
    ("existing_options", "env_port", "cli_args", "expected_port"),
    [
        (None, None, [], "None"),
        ("db_port = 5432\nx_sendfile = True", None, [], "5432"),
        ("db_port = 5432\nx_sendfile = True", 6543, [], "6543"),
        ("db_port = 5432\nx_sendfile = True", None, ["--db-port", "6544"], "6544"),
        ("db_port = 5432\nx_sendfile = True", 0, [], "0"),
        ("db_port = 5432\nx_sendfile = True", None, ["--db-port", "0"], "0"),
        ("db_port = 0\nx_sendfile = True", None, [], "0"),
        ("db_port = None\nx_sendfile = True", None, [], "None"),
        ("db_port = False\nx_sendfile = True", None, [], "None"),
        ("x_sendfile = True", None, [], "None"),
    ],
)
def test_test_run_prepares_selected_config_before_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_options: str | None,
    env_port: int | None,
    cli_args: list[str],
    expected_port: str,
) -> None:
    """The test command writes its effective configuration before bootstrap starts."""
    config = _config(tmp_path)
    if existing_options is not None:
        config.odoo_conf_path.write_text(f"[options]\n{existing_options}\n", encoding="utf-8")
    module = SimpleNamespace(name="sale")
    monkeypatch.setattr(
        test_run,
        "GodooModules",
        lambda _paths: SimpleNamespace(
            get_modules=lambda _names: [module],
            get_module_dependencies=lambda _module: [],
        ),
    )
    monkeypatch.setattr(test_run, "require_cli_odoo_version", lambda *_args: SimpleNamespace(major=19))
    monkeypatch.setattr("godoo_cli.runtime.odoo.require_supported_odoo_runtime", lambda _path: None)

    bootstrap_called = False

    def bootstrap(**kwargs: object) -> int:
        nonlocal bootstrap_called
        bootstrap_called = True
        prepared = kwargs["config"]
        assert isinstance(prepared, GodooConfig)
        assert prepared.odoo_conf_path.exists()
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(prepared.odoo_conf_path, encoding="utf-8")
        assert parser["options"].get("db_name") == "selected_test_db"
        assert parser["options"].get("dbfilter") == "^selected_test_db$"
        assert parser["options"].get("db_port") == expected_port
        assert parser["options"].get("x_sendfile") == "False"
        return 0

    monkeypatch.setattr(test_run, "bootstrap_and_prep_launch_cmd", bootstrap)
    env = {
        "ODOO_MAIN_FOLDER": str(config.odoo_install_folder),
        "ODOO_WORKSPACE_ADDON_LOCATION": str(config.workspace_addon_path),
        "ODOO_CONF_PATH": str(config.odoo_conf_path),
        "ODOO_DB_FILTER": config.db_filter,
        "ODOO_DB_USER": "odoo",
        "ODOO_MAIN_DB": config.db_name,
    }
    env["ODOO_DB_PORT"] = str(env_port) if env_port is not None else None
    result = CliRunner().invoke(test_cli_app(), ["run", "sale", *cli_args], env=env)

    assert result.exit_code == 0, result.output
    assert bootstrap_called


@pytest.mark.parametrize("test_module_names", [[], ["unknown"]])
def test_test_run_does_not_write_config_without_runnable_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_module_names: list[str]
) -> None:
    """An empty or unresolved module selection leaves the configuration file untouched."""
    config = _config(tmp_path)
    monkeypatch.setattr(test_run, "resolve_command_config", lambda **_kwargs: config)
    monkeypatch.setattr(test_run, "require_cli_odoo_version", lambda *_args: SimpleNamespace(major=19))
    monkeypatch.setattr(
        test_run,
        "GodooModules",
        lambda _paths: SimpleNamespace(get_modules=lambda _names: [], get_module_dependencies=lambda _module: []),
    )
    monkeypatch.setattr(test_run, "prepare_runtime", lambda *_args, **_kwargs: pytest.fail("must not prepare"))
    test_run.odoo_run_tests(
        test_module_names=test_module_names,
        odoo_main_path=config.odoo_install_folder,
        workspace_addon_path=config.workspace_addon_path,
        odoo_conf_path=config.odoo_conf_path,
        db_filter=config.db_filter,
        db_user=config.db_user,
        db_name=config.db_name,
    )

    assert not config.odoo_conf_path.exists()


def test_test_run_does_not_write_config_when_version_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected Odoo version stops before test configuration is persisted."""
    config = _config(tmp_path)
    monkeypatch.setattr(test_run, "resolve_command_config", lambda **_kwargs: config)

    def reject_version(*_args: object) -> None:
        message = "Odoo 19 is required"
        raise BadParameter(message)

    monkeypatch.setattr(test_run, "require_cli_odoo_version", reject_version)
    monkeypatch.setattr(test_run, "prepare_runtime", lambda *_args, **_kwargs: pytest.fail("must not prepare"))

    with pytest.raises(BadParameter, match="Odoo 19 is required"):
        test_run.odoo_run_tests(
            test_module_names=["sale"],
            odoo_main_path=config.odoo_install_folder,
            workspace_addon_path=config.workspace_addon_path,
            odoo_conf_path=config.odoo_conf_path,
            db_filter=config.db_filter,
            db_user=config.db_user,
            db_name=config.db_name,
        )

    assert not config.odoo_conf_path.exists()
