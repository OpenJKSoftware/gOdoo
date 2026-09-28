"""Verify native Git worktree reporting and pruning in disposable repositories."""

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.commands.workspace import workspace_gc
from godoo_cli.commands.workspace import workspace_storage as workspace_storage_command
from godoo_cli.git import repository
from godoo_cli.workspace.git import managed_pool
from godoo_cli.workspace.storage import prune_sources, storage_report
from godoo_cli.workspace.types import WorkspaceError


def _git(path: Path, *args: str) -> str:
    """Run Git in a disposable fixture repository."""
    return repository(path).run("-C", str(path), *args)


def _sources(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Create a bare pool with branch and detached worktrees."""
    working = tmp_path / "working"
    working.mkdir()
    _git(working, "init", "-b", "19.0")
    _git(working, "config", "user.name", "Test")
    _git(working, "config", "user.email", "test@example.test")
    (working / "source.txt").write_text("source contents\n", encoding="utf-8")
    _git(working, "add", "source.txt")
    _git(working, "commit", "-m", "fixture")
    root = tmp_path / "sources"
    pool = root / "odoo" / ".git"
    pool.parent.mkdir(parents=True)
    _git(working, "clone", "--bare", str(working), str(pool))
    (pool.parent / ".godoo.lock").touch()
    checkout = pool.parent / "worktrees" / "19.0"
    detached = pool.parent / "worktrees" / "detached"
    _git(pool, "worktree", "add", str(checkout), "19.0")
    _git(pool, "worktree", "add", "--detach", str(detached), "19.0")
    return root, pool, checkout, detached


def _record(report: dict[str, Any], path: Path) -> dict[str, Any]:
    """Return one native worktree record from a storage report."""
    return next(record for record in report["worktrees"] if record["path"] == str(path))


def _metadata(pool: Path) -> dict[Path, tuple[bytes, int]]:
    """Capture Git metadata bytes and modification times."""
    return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in pool.rglob("*") if path.is_file()}


def test_storage_lists_native_branches_and_locks(tmp_path: Path) -> None:
    """Report Git's worktree records without changing their metadata."""
    root, pool, checkout, detached = _sources(tmp_path)
    _git(pool, "worktree", "lock", "--reason", "Keep for release\nreview", str(checkout))
    unusual = pool.parent / "worktrees" / "path with\nnewline"
    _git(pool, "worktree", "add", "--detach", str(unusual), "19.0")
    before = _metadata(pool)

    report = storage_report(root)

    current = _record(report, checkout)
    assert report["pools"] == [{"path": str(pool)}]
    assert current["branch"] == "refs/heads/19.0"
    assert current["locked"] is True
    assert current["lock_reason"] == "Keep for release\nreview"
    assert current["detached"] is False
    assert current["prunable"] is False
    assert _record(report, detached)["detached"] is True
    assert _record(report, unusual)["path"] == str(unusual)
    assert not {"bytes", "legacy_snapshot"} & current.keys()
    assert _metadata(pool) == before
    assert not (root / ".godoo-storage.lock").exists()


def test_storage_discovers_only_fixed_pool_layout(tmp_path: Path) -> None:
    """Ignore legacy, nested, and symlinked pools outside the managed layout."""
    root, pool, _checkout, _detached = _sources(tmp_path)
    thirdparty = root / "odoo_thirdparty" / "OCA_fixture" / ".git"
    thirdparty.parent.mkdir(parents=True)
    _git(pool, "clone", "--bare", str(pool), str(thirdparty))
    (thirdparty.parent / ".godoo.lock").touch()
    nested = root / "odoo" / "nested" / "too-deep" / ".git"
    nested.parent.mkdir(parents=True)
    _git(pool, "clone", "--bare", str(pool), str(nested))
    legacy = root / ".git"
    _git(pool, "clone", "--bare", str(pool), str(legacy))
    (root / "odoo" / "linked").symlink_to(pool.parent, target_is_directory=True)

    report = storage_report(root)

    assert report["pools"] == [{"path": str(pool)}, {"path": str(thirdparty)}]
    assert str(nested) not in {record["pool"] for record in report["worktrees"]}
    assert str(legacy) not in {record["pool"] for record in report["worktrees"]}


def test_storage_ignores_a_markerless_canonical_bare_pool(tmp_path: Path) -> None:
    """Only a pool with gOdoo's ownership marker is eligible for storage operations."""
    root, pool, _checkout, _detached = _sources(tmp_path)
    (pool.parent / ".godoo.lock").unlink()

    assert managed_pool(pool.parent) is False
    assert storage_report(root)["pools"] == []
    assert prune_sources(root, apply=True)["pools"] == []


def test_storage_lists_external_worktrees_without_traversing_them(tmp_path: Path) -> None:
    """Keep Git's external record while avoiding filesystem inspection of its checkout."""
    root, pool, checkout, _detached = _sources(tmp_path)
    external = tmp_path / "external"
    _git(pool, "worktree", "add", "--detach", str(external), "19.0")
    moved = tmp_path / "moved"
    checkout.rename(moved)
    checkout.symlink_to(moved, target_is_directory=True)

    report = storage_report(root)

    assert _record(report, checkout)["path"] == str(checkout)
    assert _record(report, external)["path"] == str(external)
    assert checkout.is_symlink()
    assert external.is_dir()


def test_prune_previews_then_removes_only_stale_administrative_records(tmp_path: Path) -> None:
    """Use Git pruning without touching worktrees, branches, or local files."""
    root, pool, checkout, detached = _sources(tmp_path)
    stale = pool.parent / "worktrees" / "stale"
    _git(pool, "worktree", "add", "-b", "stale", str(stale), "19.0")
    stale_gitdir = Path(_git(stale, "rev-parse", "--absolute-git-dir"))
    shutil.rmtree(stale)
    old = time.time() - 365 * 86400
    os.utime(stale_gitdir / "gitdir", (old, old))
    (checkout / "local.txt").write_text("keep local changes\n", encoding="utf-8")
    branch_refs = _git(pool, "for-each-ref", "--format=%(refname) %(objectname)")
    before = _metadata(pool)

    assert _record(storage_report(root), stale)["prunable"] is True
    preview = prune_sources(root)

    assert preview["applied"] is False
    assert "stale" in preview["pools"][0]["output"]
    assert _metadata(pool) == before

    applied = prune_sources(root, apply=True)

    assert applied["applied"] is True
    assert "stale" in applied["pools"][0]["output"]
    assert not stale_gitdir.exists()
    assert _git(pool, "for-each-ref", "--format=%(refname) %(objectname)") == branch_refs
    assert (checkout / "local.txt").read_text(encoding="utf-8") == "keep local changes\n"
    assert (detached / "source.txt").is_file()
    assert len(storage_report(root)["worktrees"]) == 2
    assert not (root / ".godoo-storage.lock").exists()


def test_native_lock_preserves_missing_worktree_record(tmp_path: Path) -> None:
    """Leave a locked missing worktree record to Git's native lock handling."""
    root, pool, checkout, _detached = _sources(tmp_path)
    _git(pool, "worktree", "lock", "--reason", "offline volume", str(checkout))
    gitdir = Path(_git(checkout, "rev-parse", "--absolute-git-dir"))
    shutil.rmtree(checkout)
    old = time.time() - 365 * 86400
    os.utime(gitdir / "gitdir", (old, old))

    result = prune_sources(root, apply=True)

    assert result["pools"][0]["output"] == ""
    assert gitdir.is_dir()
    assert _record(storage_report(root), checkout)["lock_reason"] == "offline volume"


def test_storage_and_gc_reject_relative_or_project_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Storage commands retain the workspace external absolute-root contract."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    with pytest.raises(WorkspaceError, match="absolute host path"):
        storage_report(Path("."))
    with pytest.raises(WorkspaceError, match="outside the project"):
        prune_sources(project)
    _assert_storage_and_gc_leave_missing_root_absent(tmp_path)


def _assert_storage_and_gc_leave_missing_root_absent(tmp_path: Path) -> None:
    """Avoid creating a missing shared source root while reporting or pruning."""
    root = tmp_path / "missing"

    assert storage_report(root) == {"sources_root": str(root), "pools": [], "worktrees": []}
    assert prune_sources(root, apply=True)["pools"] == []
    assert not root.exists()


def test_cli_reports_worktrees_without_project_export_inventory(tmp_path: Path) -> None:
    """Keep CLI JSON focused on native source worktree state."""
    root, pool, checkout, detached = _sources(tmp_path)
    app = typer.Typer()
    app.command("storage")(workspace_storage_command)
    app.command("gc")(workspace_gc)
    runner = CliRunner()

    report = runner.invoke(app, ["storage", "--sources-root", str(root), "--json"])
    preview = runner.invoke(
        app,
        ["gc", "--sources-root", str(root), "--json"],
        env={"GODOO_GC_APPLY": "1"},
    )
    applied = runner.invoke(app, ["gc", "--sources-root", str(root), "--apply", "--json"])

    assert report.exit_code == 0, report.output
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.stdout)["applied"] is False
    assert json.loads(report.stdout) == {
        "pools": [{"path": str(pool)}],
        "sources_root": str(root),
        "worktrees": [
            {
                "branch": "refs/heads/19.0",
                "commit": _git(checkout, "rev-parse", "HEAD"),
                "detached": False,
                "lock_reason": None,
                "locked": False,
                "path": str(checkout),
                "pool": str(pool),
                "prunable": False,
                "prunable_reason": None,
            },
            {
                "branch": None,
                "commit": _git(detached, "rev-parse", "HEAD"),
                "detached": True,
                "lock_reason": None,
                "locked": False,
                "path": str(detached),
                "pool": str(pool),
                "prunable": False,
                "prunable_reason": None,
            },
        ],
    }
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["applied"] is True
    assert checkout.is_dir()
    assert detached.is_dir()
