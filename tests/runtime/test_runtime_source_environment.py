"""Contract tests for host-resolved runtime source paths."""

import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from typer import BadParameter

from godoo_cli.commands.configuration import _resolve_development_source_config, resolve_command_config


def _resolve(tmp_path: Path):
    """Resolve a minimal command configuration."""
    return resolve_command_config(
        odoo_main_path=tmp_path / "configured-odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "workspace-addons",
    )


def test_runtime_environment_bypasses_workspace_resolution_and_preserves_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime paths come directly from the host instead of workspace inspection."""
    sources_root = tmp_path / "sources"
    odoo_path = sources_root / "odoo"
    addon_paths = (sources_root / "first-addon", sources_root / "second-addon")
    for path in (odoo_path, *addon_paths):
        path.mkdir(parents=True)
    monkeypatch.setenv("GODOO_SOURCES_ROOT", str(sources_root))
    monkeypatch.setenv("GODOO_RUNTIME_ODOO_PATH", str(odoo_path))
    monkeypatch.setenv("GODOO_RUNTIME_ADDON_PATHS", os.pathsep.join(str(path) for path in addon_paths))
    monkeypatch.setenv("GODOO_RUNTIME_MATERIALIZED", "1")

    with patch(
        "godoo_cli.commands.configuration._development_sources",
        side_effect=AssertionError("runtime must not inspect the workspace"),
    ):
        config = _resolve(tmp_path)

    assert config.odoo_install_folder == odoo_path
    assert config.resolved_addon_paths == addon_paths


def test_materialized_runtime_discovers_canonical_addon_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Materialized runtime ignores stale config-file addon paths."""
    config = _resolve(tmp_path)
    stale_addon = tmp_path / "stale-addon"
    odoo_addons = config.odoo_install_folder / "addons"
    odoo_nested_addons = config.odoo_install_folder / "odoo" / "addons"
    workspace_addons = config.workspace_addon_path
    thirdparty_repo = config.thirdparty_addon_path / "OCA-addons"
    for addon_root in (odoo_addons, odoo_nested_addons, workspace_addons):
        addon_root.mkdir(parents=True)
    workspace_module = workspace_addons / "project_module"
    workspace_module.mkdir()
    (workspace_module / "__manifest__.py").write_text("{}", encoding="utf-8")
    module = thirdparty_repo / "sample_module"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GODOO_RUNTIME_MATERIALIZED", "1")
    monkeypatch.delenv("GODOO_RUNTIME_ODOO_PATH", raising=False)
    monkeypatch.delenv("GODOO_RUNTIME_ADDON_PATHS", raising=False)

    config = _resolve_development_source_config(replace(config, resolved_addon_paths=(stale_addon,)))

    assert config.resolved_addon_paths is None
    assert config.addon_paths == [
        odoo_addons,
        odoo_nested_addons,
        workspace_addons,
        thirdparty_repo,
    ]


def test_materialized_runtime_uses_selected_odoo_root_for_thirdparty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Find materialized addons without a third-party location setting."""
    odoo_root = tmp_path / "image" / "odoo"
    thirdparty_repo = odoo_root.parent / "thirdparty" / "OCA-addons"
    module = thirdparty_repo / "sample_module"
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GODOO_RUNTIME_MATERIALIZED", "1")
    monkeypatch.delenv("GODOO_RUNTIME_ODOO_PATH", raising=False)
    monkeypatch.delenv("GODOO_RUNTIME_ADDON_PATHS", raising=False)

    config = resolve_command_config(
        odoo_main_path=odoo_root,
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
    )

    assert config.addon_paths == [thirdparty_repo]


@pytest.mark.parametrize(
    ("odoo_path", "addon_paths", "message"),
    [
        (None, "present", "must be provided together"),
        ("present", None, "must be provided together"),
        ("relative/odoo", "relative/addons", "must be absolute"),
    ],
)
def test_runtime_environment_rejects_partial_or_relative_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    odoo_path: str | None,
    addon_paths: str | None,
    message: str,
) -> None:
    """Runtime paths fail before any workspace or Git inspection."""
    if odoo_path is not None:
        monkeypatch.setenv("GODOO_RUNTIME_ODOO_PATH", odoo_path)
    else:
        monkeypatch.delenv("GODOO_RUNTIME_ODOO_PATH", raising=False)
    if addon_paths is not None:
        monkeypatch.setenv("GODOO_RUNTIME_ADDON_PATHS", addon_paths)
    else:
        monkeypatch.delenv("GODOO_RUNTIME_ADDON_PATHS", raising=False)

    with pytest.raises(BadParameter, match=message):
        _resolve(tmp_path)


def test_runtime_environment_rejects_paths_escaping_source_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime paths cannot escape the read-only source-root mount."""
    sources_root = tmp_path / "sources"
    odoo_path = sources_root / "odoo"
    addon_path = tmp_path / "outside-addon"
    odoo_path.mkdir(parents=True)
    addon_path.mkdir()
    monkeypatch.setenv("GODOO_SOURCES_ROOT", str(sources_root))
    monkeypatch.setenv("GODOO_RUNTIME_ODOO_PATH", str(odoo_path))
    monkeypatch.setenv("GODOO_RUNTIME_ADDON_PATHS", str(addon_path))

    with pytest.raises(BadParameter, match="escapes GODOO_SOURCES_ROOT"):
        _resolve(tmp_path)


def test_runtime_environment_allows_project_addons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The project addons bind mount is an allowed development source."""
    project_root = tmp_path / "project"
    sources_root = tmp_path / "sources"
    odoo_path = sources_root / "odoo"
    project_addons = project_root / "addons"
    odoo_path.mkdir(parents=True)
    project_addons.mkdir(parents=True)
    monkeypatch.chdir(project_root)
    monkeypatch.setenv("GODOO_SOURCES_ROOT", str(sources_root))
    monkeypatch.setenv("GODOO_RUNTIME_ODOO_PATH", str(odoo_path))
    monkeypatch.setenv("GODOO_RUNTIME_ADDON_PATHS", str(project_addons))

    config = _resolve(tmp_path)

    assert config.resolved_addon_paths == (project_addons,)


def test_runtime_environment_rejects_symlink_escaping_source_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink below the mount cannot point at an unmounted source."""
    sources_root = tmp_path / "sources"
    odoo_path = sources_root / "odoo"
    outside_addon = tmp_path / "outside-addon"
    linked_addon = sources_root / "linked-addon"
    odoo_path.mkdir(parents=True)
    outside_addon.mkdir()
    linked_addon.symlink_to(outside_addon, target_is_directory=True)
    monkeypatch.setenv("GODOO_SOURCES_ROOT", str(sources_root))
    monkeypatch.setenv("GODOO_RUNTIME_ODOO_PATH", str(odoo_path))
    monkeypatch.setenv("GODOO_RUNTIME_ADDON_PATHS", str(linked_addon))

    with pytest.raises(BadParameter, match="escapes GODOO_SOURCES_ROOT"):
        _resolve(tmp_path)


def test_runtime_environment_rejects_symlink_escaping_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project-addon symlink cannot escape its project bind mount."""
    project_root = tmp_path / "project"
    sources_root = tmp_path / "sources"
    odoo_path = sources_root / "odoo"
    outside_addon = tmp_path / "outside-addon"
    linked_addon = project_root / "addons"
    odoo_path.mkdir(parents=True)
    outside_addon.mkdir()
    project_root.mkdir()
    linked_addon.symlink_to(outside_addon, target_is_directory=True)
    monkeypatch.chdir(project_root)
    monkeypatch.setenv("GODOO_SOURCES_ROOT", str(sources_root))
    monkeypatch.setenv("GODOO_RUNTIME_ODOO_PATH", str(odoo_path))
    monkeypatch.setenv("GODOO_RUNTIME_ADDON_PATHS", str(linked_addon))

    with pytest.raises(BadParameter, match="escapes GODOO_SOURCES_ROOT"):
        _resolve(tmp_path)
