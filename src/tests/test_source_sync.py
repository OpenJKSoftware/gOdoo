"""Regression tests for reusable source synchronization."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from godoo_cli.commands import source_get
from godoo_cli.models import GodooConfig


def test_sync_source_uses_argument_vector_for_requirements(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Requirement paths with spaces must not cross a shell boundary."""
    odoo_path = tmp_path / "odoo source"
    config = GodooConfig(
        odoo_install_folder=odoo_path,
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        source_get.GodooManifest,
        "from_yaml_file",
        lambda _path: SimpleNamespace(odoo=object(), default_branch="main"),
    )
    monkeypatch.setattr(source_get, "git_ensure_repo_matches_manifest", lambda **_kwargs: None)
    monkeypatch.setattr(source_get, "pip_command", lambda: "'/pip path/python' -m pip")
    monkeypatch.setattr(source_get, "run_cmd", lambda command: calls.append(command))

    source_get.sync_source(
        config,
        manifest_path=tmp_path / "manifest.yml",
        thirdparty_zip_source=tmp_path / "archives",
        update_mode=source_get.UpdateMode.odoo,
    )

    assert calls == [["/pip path/python", "-m", "pip", "install", "-r", str(odoo_path / "requirements.txt")]]
