"""Tests for Odoo-native database lifecycle wrappers."""

from pathlib import Path

from godoo_cli.commands.db.archive import dump_runtime_archive, load_runtime_archive
from godoo_cli.commands.db.reset import reset_empty_runtime, reset_runtime_from_template


def test_template_reset_delegates_to_odoo_duplicate_with_data_dir():
    commands = []
    result = reset_runtime_from_template(
        db_name="runtime",
        db_template_name="template",
        odoo_bin_path=Path("/odoo/odoo-bin"),
        odoo_conf_path=Path("/project/odoo.conf"),
        data_dir=Path("/var/lib/odoo"),
        runner=lambda command: commands.append(command) or 17,
    )
    assert result == 17
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
            "template",
            "runtime",
        ]
    ]


def test_empty_reset_delegates_to_odoo_drop_and_preserves_return_code():
    commands = []
    result = reset_empty_runtime(
        db_name="runtime",
        odoo_bin_path=Path("/odoo/odoo-bin"),
        runner=lambda command: commands.append(command) or 9,
    )
    assert result == 9
    assert commands == [["/odoo/odoo-bin", "db", "drop", "runtime"]]


def test_dump_delegates_to_odoo_zip_archive_with_its_filestore():
    commands = []
    result = dump_runtime_archive(
        db_name="template",
        archive_path=Path("/archives/template.zip"),
        odoo_bin_path=Path("/odoo/odoo-bin"),
        odoo_conf_path=Path("/project/odoo.conf"),
        data_dir=Path("/var/lib/odoo"),
        runner=lambda command: commands.append(command) or 7,
    )
    assert result == 7
    assert commands == [
        [
            "/odoo/odoo-bin",
            "db",
            "--config",
            "/project/odoo.conf",
            "--data-dir",
            "/var/lib/odoo",
            "dump",
            "template",
            "/archives/template.zip",
        ]
    ]


def test_load_delegates_to_odoo_zip_archive_and_preserves_return_code():
    commands = []
    result = load_runtime_archive(
        db_name="template",
        archive_path=Path("/archives/template.zip"),
        odoo_bin_path=Path("/odoo/odoo-bin"),
        force=True,
        runner=lambda command: commands.append(command) or 13,
    )
    assert result == 13
    assert commands == [
        [
            "/odoo/odoo-bin",
            "db",
            "load",
            "--force",
            "template",
            "/archives/template.zip",
        ]
    ]
