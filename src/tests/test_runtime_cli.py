"""CLI contract tests for the canonical runtime lifecycle surface."""

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli import commands as cmd
from godoo_cli.cli import main_cli
from godoo_cli.commands import lifecycle as lifecycle_commands
from godoo_cli.commands.db.query import is_bootstrapped


def _registered_commands(app: typer.Typer):
    return {command.name: command.callback for command in app.registered_commands}


def test_runtime_commands_are_registered_as_thin_delegates():
    commands = _registered_commands(cmd.runtime_cli_app())

    assert commands == {
        "prepare": cmd.prepare_odoo,
        "bootstrap": cmd.bootstrap_odoo_runtime,
        "reconcile": cmd.reconcile_odoo_runtime,
        "init": cmd.deployment_init_odoo_runtime,
        "launch": cmd.launch_odoo,
        "status": is_bootstrapped,
    }


def test_runtime_bootstrap_only_initializes_the_guarded_runtime(monkeypatch: pytest.MonkeyPatch):
    observed: dict[str, object] = {}

    def ensure(_config: object, **kwargs: object) -> bool:
        observed.update(kwargs)
        return True

    monkeypatch.setattr(lifecycle_commands, "ensure_runtime", ensure)
    result = CliRunner().invoke(
        main_cli(),
        ["runtime", "bootstrap"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_THIRDPARTY_LOCATION": "/tmp/thirdparty",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
        },
    )

    assert result.exit_code == 0, result.output
    assert observed["prepare_bootstrap_dependencies"] is False
    preparer = observed["preparer"]
    assert callable(preparer)
    assert preparer(object()) is None


def test_runtime_help_lists_the_canonical_lifecycle_commands():
    result = CliRunner().invoke(main_cli(), ["runtime", "--help"])

    assert result.exit_code == 0
    for command in ("prepare", "bootstrap", "reconcile", "init", "launch", "status"):
        assert command in result.output


def test_existing_top_level_lifecycle_aliases_remain_registered():
    result = CliRunner().invoke(main_cli(), ["--help"])

    assert result.exit_code == 0
    for command in ("prepare", "bootstrap", "reconcile-runtime", "deployment-init", "launch"):
        assert command in result.output


def test_runtime_launch_help_keeps_the_start_only_contract():
    result = CliRunner().invoke(main_cli(), ["runtime", "launch", "--help"])

    assert result.exit_code == 0
    assert "without preparing dependencies or changing database state" in result.output


def test_runtime_init_help_exposes_canonical_and_deprecated_controls():
    result = CliRunner().invoke(main_cli(), ["runtime", "init", "--help"], terminal_width=220)

    assert result.exit_code == 0
    assert "--seed" in result.output
    assert "GODOO_RUNTIME_SEED" in result.output
    assert "--seed-archive" in result.output
    assert "GODOO_SEED_ARCHIVE" in result.output
    # Rich elides long option and environment names at the fixed help width.
    assert "--pre-launch-hooks-" in result.output
    assert "GODOO_PRE_LAUNCH_HOO" in result.output
    assert "--after-bootstrap-" in result.output
    assert "--after-restore-dir" in result.output
    assert "--after-reconcile-" in result.output
    for option in ("--upgrade-path", "--pre-upgrade-script", "--log-handler", "--x-sendfile"):
        assert option in result.output


def test_legacy_backup_dump_alias_remains_registered():
    result = CliRunner().invoke(main_cli(), ["backup", "--help"])

    assert result.exit_code == 0
    assert "dump" in result.output
