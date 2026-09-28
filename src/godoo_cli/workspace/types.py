"""Neutral value types shared by workspace orchestration and Git adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WorkspaceError(RuntimeError):
    """Raised when managed workspace state is unsafe, stale, or malformed."""


def validate_sources_root(project_root: Path, sources_root: Path) -> Path:
    """Resolve and enforce the external shared-source-root contract."""
    project = project_root.expanduser().resolve()
    if not sources_root.expanduser().is_absolute():
        message = "GODOO_SOURCES_ROOT must be an absolute host path."
        raise WorkspaceError(message)
    resolved_source_root = sources_root.expanduser().resolve()
    if resolved_source_root == project or project in resolved_source_root.parents:
        message = f"Managed source path must be outside the project: {resolved_source_root}"
        raise WorkspaceError(message)
    return resolved_source_root


@dataclass(frozen=True)
class WorkspaceSettings:
    """Resolved host paths and generated-state locations for one project."""

    project_root: Path
    manifest_path: Path
    sources_root: Path
    debug_port: int = 5678

    @property
    def state_dir(self) -> Path:
        """Return project-local generated state directory."""
        return self.project_root / ".godoo"

    @property
    def odoo_source_root(self) -> Path:
        """Return managed Odoo repository root."""
        return self.sources_root / "odoo"

    @property
    def thirdparty_source_root(self) -> Path:
        """Return managed third-party repository root."""
        return self.sources_root / "odoo_thirdparty"

    @classmethod
    def create(
        cls,
        *,
        project_root: Path,
        manifest_path: Path,
        sources_root: Path,
        debug_port: int = 5678,
    ) -> WorkspaceSettings:
        """Create settings after validating portable workspace inputs."""
        project = project_root.expanduser().resolve()
        manifest = manifest_path.expanduser()
        if not manifest.is_absolute():
            manifest = project / manifest
        if not 1 <= debug_port <= 65535:
            message = f"Debug port must be between 1 and 65535: {debug_port}"
            raise WorkspaceError(message)
        return cls(
            project_root=project,
            manifest_path=manifest.resolve(),
            sources_root=validate_sources_root(project, sources_root),
            debug_port=debug_port,
        )


@dataclass(frozen=True)
class ResolvedSource:
    """One managed source checkout selected by the manifest."""

    role: str
    prefix: str
    name: str
    url: str
    branch: str
    worktree_branch: str
    requested_commit: str
    base_commit: str
    resolved_commit: str
    merge_from: tuple[dict[str, str], ...]
    merge_commits: tuple[str, ...]
    recipe_fingerprint: str
    host_path: Path
    container_path: Path

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible source description."""
        return {
            "role": self.role,
            "prefix": self.prefix,
            "name": self.name,
            "url": self.url,
            "branch": self.branch,
            "worktree_branch": self.worktree_branch,
            "requested_commit": self.requested_commit,
            "base_commit": self.base_commit,
            "resolved_commit": self.resolved_commit,
            "merge_from": list(self.merge_from),
            "merge_commits": list(self.merge_commits),
            "recipe_fingerprint": self.recipe_fingerprint,
            "host_path": str(self.host_path),
            "container_path": str(self.container_path),
        }
