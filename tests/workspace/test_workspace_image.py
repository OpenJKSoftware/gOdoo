"""Production image source materialization contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from godoo_cli.commands.workspace import workspace_cli_app
from godoo_cli.git import repository
from godoo_cli.models import GodooGitRepo
from godoo_cli.workspace.git import repository_lock, repository_root, sync_repo_unlocked
from godoo_cli.workspace.image import materialize_workspace_image
from godoo_cli.workspace.types import ResolvedSource, WorkspaceError, WorkspaceSettings


def _source(root: Path, *, target: str = "/odoo/odoo") -> ResolvedSource:
    """Create one selected Odoo checkout for resolver tests."""
    checkout = root / "odoo" / "19.0"
    (checkout / "odoo").mkdir(parents=True)
    (checkout / "odoo" / "__init__.py").write_text("", encoding="utf-8")
    (checkout / ".git").write_text("gitdir: ../pool/worktrees/odoo\n", encoding="utf-8")
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
        host_path=checkout,
        container_path=Path(target),
    )


def _project(root: Path) -> Path:
    """Create project inputs with trees that production must leave out."""
    root.mkdir(parents=True)
    (root / "odoo_manifest.yml").write_text("odoo: {}\n", encoding="utf-8")
    (root / "addons" / "custom addon").mkdir(parents=True)
    (root / "addons" / ".git").mkdir()
    (root / "hooks" / "after build").mkdir(parents=True)
    (root / "hooks" / "after build" / "policy.py").write_text("print('hook')\n", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "odoo.conf").write_text("secret = value\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=not-in-image\n", encoding="utf-8")
    return root


def _select(monkeypatch: pytest.MonkeyPatch, result: tuple[object, ...]) -> None:
    monkeypatch.setattr("godoo_cli.workspace.operations.select_workspace_sources", lambda _settings: result)


def _assert_no_host_paths(value: object) -> None:
    if isinstance(value, dict):
        assert "host_path" not in value
        for child in value.values():
            _assert_no_host_paths(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_host_paths(child)


def test_materialize_copies_selected_sources_and_portable_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Copy only selected trees, omit Git metadata, and record schema 2 provenance."""
    project = _project(tmp_path / "project path with spaces")
    sources = tmp_path / "source path with spaces"
    selected = _source(sources)
    (selected.host_path / "odoo" / ".git").mkdir()
    (selected.host_path / "odoo" / ".git" / "config").write_text("private\n", encoding="utf-8")
    ignored = sources / "odoo" / "ignored"
    ignored.mkdir(parents=True)
    archive_dir = sources / "odoo_thirdparty" / ".archives" / "addon-bundle"
    archive_dir.mkdir(parents=True)
    (archive_dir / "custom_addon").mkdir()
    archive = {
        "host_path": str(archive_dir),
        "source_path": "vendor/addon-bundle.zip",
        "container_path": "/odoo/thirdparty/addon-bundle",
        "checksum": "archive-digest",
    }
    _select(monkeypatch, ("manifest-digest", [selected], [archive]))
    destination = tmp_path / "image"

    result = materialize_workspace_image(
        project,
        project / "odoo_manifest.yml",
        sources,
        destination,
        [Path("hooks/after build")],
    )

    assert [copy["name"] for copy in result["copied"]] == [
        "odoo",
        "addon-bundle.zip",
        "addons",
        "hooks/after build",
    ]
    assert (destination / "odoo/odoo/odoo/__init__.py").is_file()
    assert not (destination / "odoo/odoo/odoo/.git").exists()
    assert (destination / "odoo/thirdparty/addon-bundle/custom_addon").is_dir()
    assert (destination / "odoo/godoo_workspace/addons/custom addon").is_dir()
    assert not (destination / "odoo/godoo_workspace/addons/.git").exists()
    assert (destination / "odoo/godoo_workspace/hooks/after build/policy.py").is_file()
    assert not (destination / "odoo/godoo_workspace/config").exists()
    assert not (destination / "odoo/godoo_workspace/.env").exists()

    provenance = json.loads((destination / "odoo/godoo-source-provenance.json").read_text(encoding="utf-8"))
    assert provenance["schema_version"] == 2
    assert provenance["manifest_sha256"] == "manifest-digest"
    assert provenance["sources"][0]["source_path"] == "odoo/19.0"
    assert provenance["archives"] == [
        {
            "container_path": "/odoo/thirdparty/addon-bundle",
            "checksum": "archive-digest",
            "source_path": "odoo_thirdparty/.archives/addon-bundle",
        }
    ]
    _assert_no_host_paths(provenance)
    assert result["provenance_path"] == str(destination / "odoo/godoo-source-provenance.json")


@pytest.mark.parametrize("hook", [Path("."), Path("../hooks"), Path("/absolute/hooks")])
def test_materialize_rejects_unsafe_project_hook_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, hook: Path
) -> None:
    """Hook paths cannot leave the project or name its root."""
    project = _project(tmp_path / "project")
    sources = tmp_path / "sources"
    sources.mkdir()
    _select(monkeypatch, ("manifest-digest", [], []))
    with pytest.raises(WorkspaceError, match="relative directory"):
        materialize_workspace_image(project, project / "odoo_manifest.yml", sources, tmp_path / "image", [hook])
    assert not (tmp_path / "image").exists()


def test_materialize_rejects_source_outside_mounted_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Resolver results cannot copy host directories outside the mounted root."""
    project = _project(tmp_path / "project")
    sources = tmp_path / "sources"
    sources.mkdir()
    selected = _source(tmp_path / "outside")
    _select(monkeypatch, ("manifest-digest", [selected], []))
    with pytest.raises(WorkspaceError, match="escapes its context"):
        materialize_workspace_image(project, project / "odoo_manifest.yml", sources, tmp_path / "image")
    assert not (tmp_path / "image").exists()


@pytest.mark.parametrize("target", ["/tmp/odoo", "/odoo", "/odoo/../outside"])
def test_materialize_rejects_unsafe_resolver_targets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: str
) -> None:
    """Resolver destinations must remain canonical paths below /odoo."""
    project = _project(tmp_path / "project")
    sources = tmp_path / "sources"
    selected = _source(sources, target=target)
    _select(monkeypatch, ("manifest-digest", [selected], []))
    with pytest.raises(WorkspaceError, match="target"):
        materialize_workspace_image(project, project / "odoo_manifest.yml", sources, tmp_path / "image")
    assert not (tmp_path / "image").exists()


def test_materialize_rejects_parent_child_target_overlap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A source cannot cover a project tree or one of its children."""
    project = _project(tmp_path / "project")
    sources = tmp_path / "sources"
    selected = _source(sources, target="/odoo/godoo_workspace")
    _select(monkeypatch, ("manifest-digest", [selected], []))
    with pytest.raises(WorkspaceError, match="targets overlap"):
        materialize_workspace_image(project, project / "odoo_manifest.yml", sources, tmp_path / "image")
    assert not (tmp_path / "image").exists()


def test_materialize_cli_accepts_explicit_paths_and_repeatable_hooks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project(tmp_path / "project")
    sources = tmp_path / "sources"
    sources.mkdir()
    (project / "hooks" / "other").mkdir()
    observed: dict[str, object] = {}

    def materialize(
        project_root: Path,
        manifest: Path,
        sources_root: Path,
        destination_root: Path,
        hooks: list[Path],
    ) -> dict[str, object]:
        observed.update(
            project=project_root,
            manifest=manifest,
            sources=sources_root,
            destination=destination_root,
            hooks=hooks,
        )
        return {"copied": [], "destination_root": str(destination_root), "provenance_path": "provenance"}

    monkeypatch.setattr("godoo_cli.commands.workspace.image.materialize_workspace_image", materialize)
    monkeypatch.chdir(project)
    result = CliRunner().invoke(
        workspace_cli_app(),
        [
            "materialize",
            "--project-root",
            str(project),
            "--manifest",
            str(project / "odoo_manifest.yml"),
            "--sources-root",
            str(sources),
            "--destination-root",
            str(tmp_path / "image"),
            "--hook-dir",
            "hooks/after build",
            "--hook-dir",
            "hooks/other",
        ],
    )

    assert result.exit_code == 0, result.output
    assert observed["hooks"] == [Path("hooks/after build"), Path("hooks/other")]
    assert "materialize" in [command.name for command in workspace_cli_app().registered_commands]
    assert "image-plan" not in [command.name for command in workspace_cli_app().registered_commands]


def test_materialize_cli_reports_unsafe_hook_without_traceback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Unsafe hooks return a workspace error through the CLI boundary."""
    project = _project(tmp_path / "project")
    sources = tmp_path / "sources"
    sources.mkdir()
    monkeypatch.chdir(project)
    result = CliRunner().invoke(
        workspace_cli_app(),
        [
            "materialize",
            "--project-root",
            str(project),
            "--manifest",
            str(project / "odoo_manifest.yml"),
            "--sources-root",
            str(sources),
            "--destination-root",
            str(tmp_path / "image"),
            "--hook-dir",
            "../outside",
        ],
    )
    assert result.exit_code == 1
    assert "Workspace materialize failed" in result.output
    assert "Traceback" not in result.output


def test_materialize_includes_live_worktree_edits_while_check_stays_strict(tmp_path: Path) -> None:
    """Fast production selection copies live files while strict check rejects dirt."""
    project = _project(tmp_path / "project")
    working = tmp_path / "working"
    working.mkdir()
    git = repository(working)
    git.run("init", "-b", "19.0")
    git.run("config", "user.name", "Test")
    git.run("config", "user.email", "test@example.test")
    (working / "shared.txt").write_text("tracked content\n", encoding="utf-8")
    git.run("add", ".")
    git.run("commit", "-m", "initial")
    remote = tmp_path / "remote.git"
    git.run("-C", str(working), "clone", "--bare", str(working), str(remote))
    project_manifest = project / "odoo_manifest.yml"
    project_manifest.write_text(f'odoo:\n  url: "{remote.as_uri()}"\n  branch: 19.0\n', encoding="utf-8")

    sources_root = tmp_path / "sources"
    settings = WorkspaceSettings.create(
        project_root=project,
        manifest_path=project_manifest,
        sources_root=sources_root,
    )
    repo = GodooGitRepo(url=remote.as_uri(), branch="19.0")
    pool_root = repository_root(settings.odoo_source_root, repo.url, role="odoo")
    with repository_lock(pool_root, repo.url):
        source = sync_repo_unlocked(
            repo,
            root=settings.odoo_source_root,
            default_branch="19.0",
            role="odoo",
        )
    (source.host_path / "shared.txt").write_text("live edit\n", encoding="utf-8")

    destination = tmp_path / "image"
    materialize_workspace_image(
        project,
        project_manifest,
        sources_root,
        destination,
    )

    assert (destination / "odoo/odoo/shared.txt").read_text(encoding="utf-8") == "live edit\n"
    from godoo_cli.workspace import resolve_workspace_sources

    with pytest.raises(WorkspaceError):
        resolve_workspace_sources(settings)
