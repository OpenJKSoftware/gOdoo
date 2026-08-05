"""Tests for the focused, immutable runtime configuration models."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from godoo_cli.models import AddonPathResolver, DatabaseSettings, GodooConfig, WorkspaceLayout


def _layout(tmp_path: Path) -> WorkspaceLayout:
    return WorkspaceLayout(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "config" / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        manifest_path=tmp_path / "odoo_manifest.yml",
        data_dir=tmp_path / "data",
    )


def test_workspace_layout_derives_runtime_paths_and_is_immutable(tmp_path: Path):
    layout = _layout(tmp_path)

    assert layout.odoo_bin_path == tmp_path / "odoo" / "odoo-bin"
    assert layout.zip_addon_path == tmp_path / "thirdparty" / "custom"
    assert layout == _layout(tmp_path)
    with pytest.raises(FrozenInstanceError):
        layout.data_dir = tmp_path / "other-data"


def test_database_settings_derives_cached_connection_and_is_immutable():
    settings = DatabaseSettings(
        db_user="odoo",
        db_password="secret",
        db_host="postgres",
        db_port=5432,
        db_name="demo",
        db_filter="demo",
    )

    connection = settings.db_connection

    assert connection is settings.db_connection
    assert connection.cli_dict == {
        "db_host": "postgres",
        "db_port": 5432,
        "db_name": "demo",
        "db_user": "odoo",
        "db_password": "secret",
    }
    assert settings == DatabaseSettings(
        db_user="odoo",
        db_password="secret",
        db_host="postgres",
        db_port=5432,
        db_name="demo",
        db_filter="demo",
    )
    with pytest.raises(FrozenInstanceError):
        settings.db_name = "other"


def test_godoo_config_exposes_focused_models_without_changing_flat_api(tmp_path: Path):
    layout = _layout(tmp_path)
    config = GodooConfig(
        odoo_install_folder=layout.odoo_install_folder,
        odoo_conf_path=layout.odoo_conf_path,
        workspace_addon_path=layout.workspace_addon_path,
        thirdparty_addon_path=layout.thirdparty_addon_path,
        manifest_path=layout.manifest_path,
        data_dir=layout.data_dir,
        db_user="odoo",
        db_password="secret",
        db_host="postgres",
        db_port=5432,
        db_name="demo",
        db_filter="demo",
    )

    assert config.workspace_layout is config.workspace_layout
    assert config.workspace_layout == layout
    assert config.database_settings is config.database_settings
    assert config.database_settings.db_filter == "demo"
    assert config.db_connection is config.database_settings.db_connection
    assert config.odoo_bin_path == layout.odoo_bin_path
    assert config.zip_addon_path == layout.zip_addon_path


def test_addon_path_resolver_is_reusable_and_ignores_missing_roots(tmp_path: Path):
    layout = _layout(tmp_path)

    assert AddonPathResolver(layout).resolve() == []
