"""Workspace source management and generated development state."""

from typing import Any

from .manifest import GodooManifest, ManifestError
from .specifications import GitMergeSource, GodooGitRepo

__all__ = [
    "GitMergeSource",
    "GodooGitRepo",
    "GodooManifest",
    "ManifestError",
    "check_workspace",
    "configure_workspace",
    "resolve_workspace_sources",
    "runtime_environment",
    "select_workspace_sources",
    "sync_workspace",
]


def __getattr__(name: str) -> Any:
    """Load operation exports only when requested."""
    if name in __all__:
        from . import operations

        return getattr(operations, name)
    message = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(message)
