"""Tests for semantic Odoo runtime compatibility checks."""

from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from godoo_cli.helpers.odoo_files import odoo_bin_get_version, require_odoo_version
from godoo_cli.models import OdooVersion


def test_require_odoo_version_accepts_a_matching_semantic_specifier(tmp_path: Path):
    with patch(
        "godoo_cli.helpers.odoo_files.odoo_bin_get_version",
        return_value=OdooVersion(text="Odoo", major=19, minor=0),
    ):
        assert require_odoo_version(tmp_path, ">=19").raw == "19.0"


def test_require_odoo_version_rejects_a_runtime_outside_semantic_specifier(tmp_path: Path):
    with (
        patch(
            "godoo_cli.helpers.odoo_files.odoo_bin_get_version",
            return_value=OdooVersion(text="Odoo", major=18, minor=0),
        ),
        pytest.raises(typer.BadParameter, match=r"matching '>=19'"),
    ):
        require_odoo_version(tmp_path, ">=19")


def test_require_odoo_version_accepts_arbitrarily_large_minor_versions(tmp_path: Path):
    with (
        patch(
            "godoo_cli.helpers.odoo_files.odoo_bin_get_version",
            return_value=OdooVersion(text="Odoo", major=19, minor=100),
        ),
    ):
        assert require_odoo_version(tmp_path, ">=19").raw == "19.100"


def test_require_odoo_version_accepts_a_newer_major_version(tmp_path: Path):
    with patch(
        "godoo_cli.helpers.odoo_files.odoo_bin_get_version",
        return_value=OdooVersion(text="Odoo", major=20, minor=0),
    ):
        assert require_odoo_version(tmp_path, ">=19").raw == "20.0"


def test_require_odoo_version_rejects_an_unverifiable_runtime(tmp_path: Path):
    with (
        patch(
            "godoo_cli.helpers.odoo_files.odoo_bin_get_version",
            side_effect=ValueError("could not execute odoo-bin"),
        ),
        pytest.raises(typer.BadParameter, match="Could not verify the Odoo runtime"),
    ):
        require_odoo_version(tmp_path, ">=19")


def test_odoo_versions_compare_by_numeric_components():
    assert OdooVersion(text="Odoo", major=18, minor=4) < OdooVersion(text="Other", major=19, minor=0)


def test_odoo_version_exposes_a_semantic_version():
    assert OdooVersion(text="Odoo", major=19, minor=100).semantic > OdooVersion(text="Odoo", major=19, minor=9).semantic


def test_odoo_bin_get_version_parses_multidigit_components(tmp_path: Path):
    with patch("godoo_cli.helpers.odoo_files.run_cmd") as run_cmd:
        run_cmd.return_value.stdout = "Odoo 19.100"
        assert odoo_bin_get_version(tmp_path).semantic == OdooVersion(text="Odoo", major=19, minor=100).semantic
