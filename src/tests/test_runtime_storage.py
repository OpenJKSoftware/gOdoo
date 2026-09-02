"""Tests for the runtime storage command group."""

from pathlib import Path

from typer.testing import CliRunner

from godoo_cli.commands.runtime.storage import clone_runtime, runtime_storage_cli_app


def test_clone_delegates_to_odoo_duplicate_with_data_dir() -> None:
    commands = []

    result = clone_runtime(
        source="source",
        target="target",
        force=True,
        odoo_bin_path=Path("/odoo/odoo-bin"),
        odoo_conf_path=Path("/project/odoo.conf"),
        data_dir=Path("/var/lib/odoo"),
        runner=lambda command: commands.append(command) or 11,
    )

    assert result == 11
    assert commands == [
        [
            "/odoo/odoo-bin",
            "db",
            "--config",
            "/project/odoo.conf",
            "--data-dir",
            "/var/lib/odoo",
            "duplicate",
            "--force",
            "source",
            "target",
        ]
    ]


def test_clone_refuses_identical_runtime_names() -> None:
    assert (
        clone_runtime(
            source="same",
            target="same",
            force=False,
            odoo_bin_path=Path("/odoo/odoo-bin"),
            runner=lambda _: 0,
        )
        == 2
    )


def test_runtime_storage_registers_canonical_commands() -> None:
    app = runtime_storage_cli_app()

    assert {command.name for command in app.registered_commands} == {"clone", "clone-cow", "drop"}
    assert {group.name for group in app.registered_groups} == {"archive"}


def test_runtime_storage_registers_archive_commands() -> None:
    runner = CliRunner()
    app = runtime_storage_cli_app()

    archive_help = runner.invoke(app, ["archive", "--help"])

    assert archive_help.exit_code == 0
    assert "create" in archive_help.output
    assert "load" in archive_help.output
