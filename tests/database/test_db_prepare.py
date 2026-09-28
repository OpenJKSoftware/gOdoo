"""Tests for database preparation strategy selection and recovery markers."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from godoo_cli.commands.db.cli import db_cli_app
from godoo_cli.runtime.locks import runtime_readiness_marker
from godoo_cli.runtime.prepare import PrepareStrategy, prepare_runtime_pair, select_prepare_strategy


def test_auto_strategy_uses_documented_order() -> None:
    """Guards the contract that auto strategy uses documented order."""
    assert select_prepare_strategy(PrepareStrategy.AUTO, cow_available=True).strategy == PrepareStrategy.COW
    assert select_prepare_strategy(PrepareStrategy.AUTO, postgres_archive=True).strategy == PrepareStrategy.POSTGRES
    assert select_prepare_strategy(PrepareStrategy.AUTO, odoo_archive=True).strategy == PrepareStrategy.ODOO
    assert select_prepare_strategy(PrepareStrategy.AUTO).strategy == PrepareStrategy.BOOTSTRAP


def test_prepare_rejects_unknown_strategy_without_traceback() -> None:
    """Guards the contract that prepare rejects unknown strategy without traceback."""
    result = CliRunner().invoke(
        db_cli_app(),
        ["prepare", "--strategy", "nonsense"],
        env={
            "ODOO_MAIN_DB": "runtime",
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
        },
    )

    assert result.exit_code == 2
    assert "Unknown database preparation strategy: nonsense" in result.output
    assert "Traceback" not in result.output


def test_prepare_keeps_marker_when_callback_returns_failure(tmp_path: Path) -> None:
    """Guards the contract that prepare keeps marker when callback returns failure."""
    with pytest.raises(RuntimeError, match="exit code 7"):
        prepare_runtime_pair(
            data_dir=tmp_path,
            db_name="runtime",
            strategy=PrepareStrategy.BOOTSTRAP,
            bootstrap=lambda: 7,
        )
    assert runtime_readiness_marker(tmp_path, "runtime").exists()


def test_prepare_clears_marker_after_successful_completion(tmp_path: Path) -> None:
    """Guards the contract that prepare clears marker after successful completion."""
    prepare_runtime_pair(
        data_dir=tmp_path,
        db_name="runtime",
        strategy=PrepareStrategy.BOOTSTRAP,
        bootstrap=lambda: True,
        completion=lambda: 0,
    )
    assert not runtime_readiness_marker(tmp_path, "runtime").exists()
