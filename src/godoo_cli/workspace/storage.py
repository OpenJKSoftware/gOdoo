"""List native Git worktrees and prune stale administrative records."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any

from .git import managed_pool, registered_worktrees, repository_lock, workspace_fail, worktree_result
from .types import validate_sources_root

_POOL_GROUPS = ("odoo", "odoo_thirdparty")


def _pool_paths(sources_root: Path) -> list[Path]:
    """Find bare repository pools in gOdoo's fixed source layout."""
    if sources_root.is_symlink() or not sources_root.is_dir():
        return []
    pools = []
    try:
        for group in _POOL_GROUPS:
            group_root = sources_root / group
            if group_root.is_symlink() or not group_root.is_dir():
                continue
            candidates = [group_root, *(path for path in group_root.iterdir() if path.is_dir())]
            for repository_root in candidates:
                pool = repository_root / ".git"
                if not repository_root.is_symlink() and managed_pool(repository_root):
                    pools.append(pool)
    except OSError as error:
        workspace_fail(f"Cannot inspect source pools in {sources_root}: {error}")
    return sorted(pools)


def _worktree_records(pool: Path) -> list[dict[str, Any]]:
    """Return Git's branch, lock, and pruning state for one repository pool."""
    records = []
    for fields in registered_worktrees(pool):
        if "worktree" not in fields or "bare" in fields:
            continue
        records.append(
            {
                "path": fields["worktree"],
                "pool": str(pool),
                "commit": fields.get("HEAD"),
                "branch": fields.get("branch"),
                "detached": "detached" in fields,
                "locked": "locked" in fields,
                "lock_reason": fields.get("locked"),
                "prunable": "prunable" in fields,
                "prunable_reason": fields.get("prunable"),
            }
        )
    return records


def storage_report(sources_root: Path) -> dict[str, Any]:
    """List repository pools and their native Git worktree records."""
    sources_root = validate_sources_root(Path.cwd(), sources_root)
    pools = _pool_paths(sources_root)
    with ExitStack() as locks:
        for pool in pools:
            locks.enter_context(repository_lock(pool.parent, write=False))
        return {
            "sources_root": str(sources_root),
            "pools": [{"path": str(pool)} for pool in pools],
            "worktrees": [record for pool in pools for record in _worktree_records(pool)],
        }


def prune_sources(sources_root: Path, *, apply: bool = False) -> dict[str, Any]:
    """Use Git's expiry and lock rules to prune stale worktree records."""
    sources_root = validate_sources_root(Path.cwd(), sources_root)
    pools = []
    discovered = _pool_paths(sources_root)
    with ExitStack() as locks:
        for pool in discovered:
            locks.enter_context(repository_lock(pool.parent, write=apply))
        for pool in discovered:
            arguments = ["prune"]
            if not apply:
                arguments.append("--dry-run")
            arguments.append("--verbose")
            returncode, stdout, stderr = worktree_result("-C", str(pool), "worktree", *arguments)
            output = "\n".join(part.strip() for part in (str(stdout), str(stderr)) if part.strip())
            if returncode:
                workspace_fail(f"Git worktree prune failed in {pool}: {output}")
            pools.append({"path": str(pool), "output": output})
    return {"sources_root": str(sources_root), "applied": apply, "pools": pools}
