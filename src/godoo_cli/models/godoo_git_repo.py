"""Model wrapping a Git Repository in the Godoo Manifest."""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from ruamel.yaml.comments import CommentedMap

from ..git.git_url import GitUrl

LOGGER = logging.getLogger(__name__)


@dataclass
class GodooGitRepo:
    """Specification for a Git repository (Odoo or third-party addon).

    Attributes:
        url: Git repository URL (HTTPS or SSH format).
        branch: Branch name. Required for Odoo repo, optional for thirdparty.
        commit: Specific commit SHA to pin. If set, skips fetch when already at this commit.
    """

    url: str
    branch: Optional[str] = None
    commit: Optional[str] = None

    @property
    def git_url(self) -> GitUrl:
        """Parsed GitUrl instance for this repository."""
        return GitUrl(self.url)

    @property
    def name(self) -> str:
        """Repository name derived from URL."""
        return self.git_url.name

    @property
    def ref(self) -> str:
        """Effective Git ref (commit if set, else branch)."""
        return self.commit or self.branch or ""

    def get_compare_url(self, to_ref: str) -> str:
        """Generate a compare URL from current commit to another ref.

        Args:
            to_ref: Target ref (branch/commit) to compare against.

        Returns:
            GitHub/GitLab compare URL, or empty string if not applicable.
        """
        if not self.commit:
            return ""
        return self.git_url.get_compare_url(self.commit, to_ref)

    def update_yaml_node(
        self,
        node: Optional[dict[str, Any]],
        add_compare_url: bool,
        default_branch: str,
    ) -> CommentedMap:
        """Update or create a YAML node for this repository."""
        if isinstance(node, CommentedMap):
            repo_node = node
        else:
            repo_node = CommentedMap()
            if node:
                repo_node.update(node)

        repo_node["url"] = self.url

        if self.branch:
            repo_node["branch"] = self.branch
        else:
            repo_node.pop("branch", None)

        if self.commit:
            repo_node["commit"] = self.commit
            if add_compare_url:
                branch = self.branch or default_branch
                compare_url = self.get_compare_url(branch)
                if compare_url and hasattr(repo_node, "yaml_add_eol_comment"):
                    repo_node.yaml_add_eol_comment(compare_url, "commit")
        else:
            repo_node.pop("commit", None)

        return repo_node

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GodooGitRepo":
        """Create RepoSpec from a dictionary (YAML node).

        Args:
            data: Dictionary with 'url', optional 'branch', optional 'commit'.

        Returns:
            New instance populated from dict.
        """
        return cls(
            url=data["url"],
            branch=data.get("branch"),
            commit=data.get("commit"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML serialization.

        Returns:
            Dictionary with non-None values only.
        """
        result: dict[str, Any] = {"url": self.url}
        if self.branch:
            result["branch"] = self.branch
        if self.commit:
            result["commit"] = self.commit
        return result

    def __eq__(self, other: object) -> bool:
        """Equality based on URL and branch only."""
        if not isinstance(other, GodooGitRepo):
            return NotImplemented
        return (self.url, self.branch or "", self.commit or "") == (other.url, other.branch or "", other.commit or "")

    def __hash__(self) -> int:
        """Hash based on URL and branch to allow set/dict usage."""
        return hash((self.url, self.branch or "", self.commit or ""))
