"""Verify static-file lookup generation at runtime command boundaries."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.commands.runtime import init as init_commands
from godoo_cli.commands.runtime import launch as launch_commands
from godoo_cli.models import GodooConfig, OdooVersion
from godoo_cli.runtime.lifecycle import LifecycleOutcome


@pytest.mark.parametrize(("command", "override_addons"), [("init", False), ("launch", False), ("launch", True)])
@pytest.mark.parametrize("via_environment", [False, True])
def test_runtime_generates_lookup_from_resolved_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, via_environment: bool, override_addons: bool
) -> None:
    root = tmp_path / "mounted-addon-repository"
    module = root / "sample" / "static"
    module.mkdir(parents=True)
    (module.parent / "__manifest__.py").write_text("{}", encoding="utf-8")
    config = GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "project-addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        resolved_addon_paths=(root,),
        data_dir=tmp_path / "data",
    )
    version = OdooVersion("Odoo Server", 19, 0)
    destination = tmp_path / "nginx-static.conf"
    commands = init_commands if command == "init" else launch_commands
    monkeypatch.setattr(commands, "resolve_command_config", lambda **_kwargs: config)
    if command == "init":
        monkeypatch.setattr(init_commands, "require_odoo_version", lambda *_args: version)
        monkeypatch.setattr(init_commands, "x_sendfile_enabled", lambda *_args: False)
        monkeypatch.setattr(init_commands, "prepare_runtime", lambda *_args, **_kwargs: None)

        def initialize(resolved: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
            preparer = kwargs["preparer"]
            assert callable(preparer)
            preparer(resolved)
            return LifecycleOutcome.READY, 0

        monkeypatch.setattr(init_commands, "deployment_init", initialize)
        function = init_commands.deployment_init_odoo_runtime
    else:
        monkeypatch.setattr(launch_commands, "require_cli_odoo_version", lambda *_args: version)
        monkeypatch.setattr(launch_commands, "require_runtime_launch_ready", lambda *_args: None)
        monkeypatch.setattr(launch_commands, "preflight_for_config", lambda *_args: None)
        monkeypatch.setattr(launch_commands, "run_odoo_command", lambda *_args: SimpleNamespace(returncode=0))
        function = launch_commands.launch_odoo
    app = typer.Typer()
    app.command()(function)
    arguments = [
        "--odoo-main-path",
        str(config.odoo_install_folder),
        "--workspace-addon-path",
        str(config.workspace_addon_path),
        "--odoo-conf-path",
        str(config.odoo_conf_path),
        "--db-filter",
        "test",
        "--db-name",
        "test",
        "--db-user",
        "odoo",
    ]
    if via_environment:
        monkeypatch.setenv("GODOO_NGINX_CONFIG_PATH", str(destination))
    else:
        monkeypatch.delenv("GODOO_NGINX_CONFIG_PATH", raising=False)
        arguments.extend(["--nginx-config", str(destination)])
    if override_addons and command == "launch":
        replacement_root = tmp_path / "overridden-addon-repository"
        replacement_static = replacement_root / "replacement" / "static"
        replacement_static.mkdir(parents=True)
        (replacement_static.parent / "__manifest__.py").write_text("{}", encoding="utf-8")
        arguments.extend(["--extra-args", f"--addons-path={replacement_root}"])

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 0, result.output
    generated = destination.read_text(encoding="utf-8")
    if override_addons and command == "launch":
        assert f'"~^replacement$" "{replacement_root}";' in generated
        assert '"~^sample$"' not in generated
    else:
        assert f'"~^sample$" "{root}";' in generated
