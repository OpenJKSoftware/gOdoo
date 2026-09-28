"""Model Git repositories declared in ``odoo_manifest.yml``."""

from dataclasses import dataclass, field
from typing import Any

from ..git.git_url import GitUrl


def _optional_string(value: Any) -> str | None:
    """Normalize YAML scalar refs while preserving missing values."""
    return str(value) if value is not None else None


def _boolean(value: Any, *, field_name: str) -> bool:
    """Read a strict boolean manifest field."""
    if value is None:
        return False
    if not isinstance(value, bool):
        message = f"Manifest field '{field_name}' must be true or false."
        raise TypeError(message)
    return value


@dataclass(frozen=True)
class GitMergeSource:
    """Describe a repository merged into a primary source."""

    url: str
    branch: str | None = None
    commit: str | None = None

    @property
    def ref(self) -> str:
        """Return the commit pin or branch selected for this source."""
        return self.commit or self.branch or ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GitMergeSource":
        """Create a merge source from YAML data."""
        url = str(data["url"])
        GitUrl(url)
        return cls(
            url=url,
            branch=_optional_string(data.get("branch")),
            commit=_optional_string(data.get("commit")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return nonempty values for YAML serialization."""
        result: dict[str, Any] = {"url": self.url}
        if self.branch:
            result["branch"] = self.branch
        if self.commit:
            result["commit"] = self.commit
        return result


@dataclass
class GodooGitRepo:
    """Describe a manifest repository and its optional merge sources."""

    url: str
    branch: str | None = None
    commit: str | None = None
    merge_from: list[GitMergeSource] = field(default_factory=list)
    worktree_branch: str | None = None
    shallow: bool = False

    @property
    def git_url(self) -> GitUrl:
        """Return the parsed repository location."""
        return GitUrl(self.url)

    @property
    def name(self) -> str:
        """Return the repository name derived from its URL."""
        return self.git_url.name

    @property
    def ref(self) -> str:
        """Return the commit pin or branch selected for this repository."""
        return self.commit or self.branch or ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GodooGitRepo":
        """Create a repository specification from YAML data."""
        merge_from_data = data.get("merge_from") or []
        url = str(data["url"])
        GitUrl(url)
        return cls(
            url=url,
            branch=_optional_string(data.get("branch")),
            commit=_optional_string(data.get("commit")),
            merge_from=[GitMergeSource.from_dict(m) for m in merge_from_data],
            worktree_branch=_optional_string(data.get("worktree_branch")),
            shallow=_boolean(data.get("shallow"), field_name="shallow"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return nonempty values for YAML serialization."""
        result: dict[str, Any] = {"url": self.url}
        if self.branch:
            result["branch"] = self.branch
        if self.commit:
            result["commit"] = self.commit
        if self.merge_from:
            result["merge_from"] = [merge.to_dict() for merge in self.merge_from]
        if self.worktree_branch:
            result["worktree_branch"] = self.worktree_branch
        if self.shallow:
            result["shallow"] = True
        return result

    def __eq__(self, other: object) -> bool:
        """Compare the primary repository location and selected ref."""
        if not isinstance(other, GodooGitRepo):
            return NotImplemented
        return (
            self.url,
            self.branch or "",
            self.commit or "",
            self.worktree_branch or "",
            self.shallow,
        ) == (
            other.url,
            other.branch or "",
            other.commit or "",
            other.worktree_branch or "",
            other.shallow,
        )

    def __hash__(self) -> int:
        """Hash the primary repository location and selected ref."""
        return hash(
            (
                self.url,
                self.branch or "",
                self.commit or "",
                self.worktree_branch or "",
                self.shallow,
            )
        )
