"""Generated workspace publication and read-only resolution contracts."""

import os
from pathlib import Path

import pytest

from godoo_cli.workspace import (
    check_workspace,
    configure_workspace,
    resolve_workspace_sources,
    runtime_environment,
)
from godoo_cli.workspace.types import ResolvedSource, WorkspaceError, WorkspaceSettings


def _settings(tmp_path: Path) -> WorkspaceSettings:
    """Build settings rooted in one temporary project."""
    project = tmp_path / "project"
    project.mkdir()
    return WorkspaceSettings(project, project / "odoo_manifest.yml", tmp_path / "sources")


def _source(tmp_path: Path) -> ResolvedSource:
    """Build a stable test Odoo selection."""
    return ResolvedSource(
        role="odoo",
        prefix="",
        name="odoo",
        url="https://github.com/odoo/odoo.git",
        branch="19.0",
        worktree_branch="godoo/19.0",
        requested_commit="",
        base_commit="base",
        resolved_commit="resolved",
        merge_from=(),
        merge_commits=(),
        recipe_fingerprint="recipe",
        host_path=tmp_path / "sources" / "odoo",
        container_path=Path("/odoo/odoo"),
    )


def test_configure_publishes_editor_workspace_and_generation_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Configure publishes editor state and retains its generation lock."""
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "godoo_cli.workspace.operations.resolve_workspace_sources",
        lambda _settings: ("digest", [_source(tmp_path)], []),
    )

    state = configure_workspace(settings)

    assert set(state) == {
        "project_root",
        "manifest_digest",
        "sources",
        "archives",
        "vscode_workspace",
        "pyright_config",
    }
    assert state["project_root"] == str(settings.project_root)
    assert Path(state["vscode_workspace"]).is_file()
    assert Path(state["pyright_config"]).is_file()
    assert (settings.state_dir / "workspace.lock").is_file()


def test_configure_removes_retired_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Configure removes obsolete generated Compose and provenance files."""
    settings = _settings(tmp_path)
    settings.state_dir.mkdir()
    retired = (
        settings.state_dir / "docker-compose.sources.yml",
        settings.state_dir / "source-provenance.json",
    )
    for path in retired:
        path.write_text("obsolete", encoding="utf-8")
    monkeypatch.setattr(
        "godoo_cli.workspace.operations.resolve_workspace_sources",
        lambda _settings: ("digest", [_source(tmp_path)], []),
    )

    configure_workspace(settings)

    assert not any(path.exists() for path in retired)


def test_check_rejects_retired_compose_and_provenance(tmp_path: Path) -> None:
    """Old generated infrastructure cannot remain active."""
    settings = _settings(tmp_path)
    settings.state_dir.mkdir()
    path = settings.state_dir / "docker-compose.sources.yml"
    path.write_text("obsolete", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="Retired generated workspace artifacts"):
        check_workspace(settings)


def test_check_sources_only_skips_generated_files_but_checks_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Production source checks do not depend on generated editor files."""
    settings = _settings(tmp_path)
    source = _source(tmp_path)
    monkeypatch.setattr(
        "godoo_cli.workspace.operations.resolve_workspace_sources",
        lambda _settings: ("digest", [source], []),
    )

    state = check_workspace(settings, sources_only=True)

    assert state["manifest_digest"] == "digest"
    assert not Path(state["vscode_workspace"]).exists()
    assert not Path(state["pyright_config"]).exists()
    with pytest.raises(WorkspaceError, match="Generated VS Code workspace is stale"):
        check_workspace(settings)


def test_check_sources_only_still_rejects_invalid_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The production preflight still fails when source validation fails."""
    settings = _settings(tmp_path)

    def invalid(_settings: WorkspaceSettings) -> tuple[str, list[ResolvedSource], list[dict[str, str]]]:
        message = "source worktree is dirty"
        raise WorkspaceError(message)

    monkeypatch.setattr("godoo_cli.workspace.operations.resolve_workspace_sources", invalid)

    with pytest.raises(WorkspaceError, match="source worktree is dirty"):
        check_workspace(settings, sources_only=True)


def test_resolve_workspace_sources_does_not_acquire_source_locks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read-only source resolution does not publish project state."""
    settings = _settings(tmp_path)
    manifest = object()
    monkeypatch.setattr("godoo_cli.workspace.operations._manifest", lambda _settings: (manifest, "digest"))
    monkeypatch.setattr(
        "godoo_cli.workspace.operations._inspect_workspace",
        lambda _settings, _manifest: ([_source(tmp_path)], []),
    )
    monkeypatch.setattr("godoo_cli.workspace.operations.sha256_file", lambda _path: "digest")

    digest, sources, archives = resolve_workspace_sources(settings)

    assert digest == "digest"
    assert sources == [_source(tmp_path)]
    assert archives == []
    assert not settings.state_dir.exists()


def test_runtime_environment_orders_existing_addon_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Runtime paths follow the existing Odoo, project, addon, archive order."""
    settings = _settings(tmp_path)
    odoo = _source(tmp_path)
    addon = ResolvedSource(
        **{
            **odoo.__dict__,
            "role": "addon",
            "name": "server tools",
            "host_path": tmp_path / "sources" / "server tools",
        }
    )
    for module_root in (
        settings.project_root / "addons" / "project_module",
        addon.host_path / "addon_module",
    ):
        module_root.mkdir(parents=True)
        (module_root / "__manifest__.py").write_text("{}", encoding="utf-8")
    archive = tmp_path / "sources" / "odoo_thirdparty" / ".archives" / "archive cache"
    archive.mkdir(parents=True)
    monkeypatch.setattr(
        "godoo_cli.workspace.operations.select_workspace_sources",
        lambda _settings: ("digest", [odoo, addon], [{"host_path": str(archive)}]),
    )

    environment = runtime_environment(settings)

    assert environment == {
        "GODOO_SOURCES_ROOT": str(settings.sources_root),
        "GODOO_RUNTIME_ODOO_PATH": str(odoo.host_path),
        "GODOO_RUNTIME_ADDON_PATHS": os.pathsep.join(
            map(
                str,
                [
                    odoo.host_path / "addons",
                    odoo.host_path / "odoo" / "addons",
                    Path("/odoo/godoo_workspace/addons"),
                    addon.host_path,
                    archive,
                ],
            )
        ),
    }
    assert not settings.state_dir.exists()
