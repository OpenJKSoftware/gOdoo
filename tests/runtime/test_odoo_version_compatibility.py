"""Tests for semantic Odoo runtime compatibility checks."""

from pathlib import Path
from unittest.mock import patch

import pytest

from godoo_cli.models import OdooVersion
from godoo_cli.runtime.odoo import OdooVersionError, odoo_bin_get_version, require_odoo_version


def test_require_odoo_version_accepts_a_matching_semantic_specifier(tmp_path: Path):
    """Guards the contract that require odoo version accepts a matching semantic specifier."""
    with patch(
        "godoo_cli.runtime.odoo.odoo_bin_get_version",
        return_value=OdooVersion(text="Odoo", major=19, minor=0),
    ):
        assert require_odoo_version(tmp_path, ">=19").raw == "19.0"


def test_require_odoo_version_rejects_a_runtime_outside_semantic_specifier(tmp_path: Path):
    """Guards the contract that require odoo version rejects a runtime outside semantic specifier."""
    with (
        patch(
            "godoo_cli.runtime.odoo.odoo_bin_get_version",
            return_value=OdooVersion(text="Odoo", major=18, minor=0),
        ),
        pytest.raises(OdooVersionError, match=r"matching '>=19'"),
    ):
        require_odoo_version(tmp_path, ">=19")


def test_require_odoo_version_accepts_arbitrarily_large_minor_versions(tmp_path: Path):
    """Guards the contract that require odoo version accepts arbitrarily large minor versions."""
    with (
        patch(
            "godoo_cli.runtime.odoo.odoo_bin_get_version",
            return_value=OdooVersion(text="Odoo", major=19, minor=100),
        ),
    ):
        assert require_odoo_version(tmp_path, ">=19").raw == "19.100"


def test_require_odoo_version_accepts_a_newer_major_version(tmp_path: Path):
    """Guards the contract that require odoo version accepts a newer major version."""
    with patch(
        "godoo_cli.runtime.odoo.odoo_bin_get_version",
        return_value=OdooVersion(text="Odoo", major=20, minor=0),
    ):
        assert require_odoo_version(tmp_path, ">=19").raw == "20.0"


def test_require_odoo_version_rejects_an_unverifiable_runtime(tmp_path: Path):
    """Guards the contract that require odoo version rejects an unverifiable runtime."""
    with (
        patch(
            "godoo_cli.runtime.odoo.odoo_bin_get_version",
            side_effect=ValueError("could not execute odoo-bin"),
        ),
        pytest.raises(OdooVersionError, match="Could not verify the Odoo runtime"),
    ):
        require_odoo_version(tmp_path, ">=19")


def test_odoo_version_exposes_a_semantic_version():
    """Guards the contract that odoo version exposes a semantic version."""
    assert OdooVersion(text="Odoo", major=19, minor=100).semantic > OdooVersion(text="Odoo", major=19, minor=9).semantic


def test_odoo_bin_get_version_parses_multidigit_components(tmp_path: Path):
    """Guards the contract that odoo bin get version parses multidigit components."""
    with patch("godoo_cli.runtime.odoo.run_odoo_command") as run_odoo_command:
        run_odoo_command.return_value.stdout = "Odoo 19.100"
        assert odoo_bin_get_version(tmp_path).semantic == OdooVersion(text="Odoo", major=19, minor=100).semantic
        run_odoo_command.assert_called_once_with(
            [str((tmp_path / "odoo-bin").absolute()), "--version"],
            capture_output=True,
            text=True,
        )
