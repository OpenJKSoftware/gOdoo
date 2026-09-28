"""GitPython facade for common repository operations."""

import logging
from pathlib import Path
from typing import Any, cast

from git import Git, Repo
from git.exc import (
    GitCommandNotFound,
    GitError,
    InvalidGitRepositoryError,
    NoSuchPathError,
)
from gitdb import GitDB, LooseObjectDB

LOGGER = logging.getLogger(__name__)

GitOutput = str | bytes
GitResult = tuple[int, GitOutput, GitOutput]


class GitRepository:
    """Expose ordinary repository operations through one GitPython facade."""

    def __init__(self, path: Path) -> None:
        """Open the repository containing path when one is available."""
        self.path = Path(path)
        try:
            self.repo: Repo | None = Repo(self.path, search_parent_directories=True)
        except (InvalidGitRepositoryError, NoSuchPathError):
            self.repo = None

    @classmethod
    def init_bare(cls, path: Path) -> "GitRepository":
        """Create and open a bare repository at path."""
        target = Path(path)
        Repo.init(target, bare=True)
        return cls(target)

    def _require_repo(self) -> Repo:
        if self.repo is None:
            message = f"Not a Git repository: {self.path}"
            raise RuntimeError(message)
        return self.repo

    @property
    def root(self) -> Path:
        """Return the working-tree root, or the bare repository path."""
        repo = self._require_repo()
        if repo.working_tree_dir:
            return Path(repo.working_tree_dir)
        return self.path

    def run(self, *arguments: str) -> str:
        """Run a regular Git command through GitPython and return its output."""
        git = (
            self.repo.git if self.repo is not None else Git(working_dir=str(self.path) if self.path.exists() else None)
        )
        return str(git.execute(["git", *arguments])).strip()

    def remote_add(self, name: str, url: str) -> None:
        """Add a named remote."""
        self._require_repo().create_remote(name, url)

    def remote_get_url(self, name: str) -> str:
        """Return a named remote configured URL."""
        return str(self._require_repo().remote(name).url)

    def is_bare(self) -> bool:
        """Return whether the repository has no working tree."""
        return bool(self._require_repo().bare)

    def is_shallow(self) -> bool:
        """Return whether the repository has a shallow object history."""
        return self._require_repo().git.rev_parse("--is-shallow-repository").strip() == "true"

    def resolve(self, expression: str) -> str:
        """Resolve a revision expression to its full commit ID."""
        return str(self._require_repo().git.rev_parse("--verify", expression)).strip()

    def metadata_resolve(self, expression: str) -> str:
        """Resolve an existing object using GitDB without starting Git."""
        repo = Repo(self.path, odbt=cast(type[LooseObjectDB], GitDB))
        return repo.commit(expression).hexsha

    def head_commit(self) -> str:
        """Return the current HEAD commit ID."""
        return self.resolve("HEAD")

    def commit_timestamp(self, expression: str) -> int:
        """Return a commit committer timestamp."""
        return int(self._require_repo().commit(expression).committed_date)

    def commit_parents(self, expression: str) -> tuple[str, ...]:
        """Return a commit parent IDs in Git order."""
        return tuple(parent.hexsha for parent in self._require_repo().commit(expression).parents)

    def commit_subject(self, expression: str) -> str:
        """Return a commit one-line subject."""
        return str(self._require_repo().commit(expression).summary)

    def tree(self, expression: str) -> str:
        """Return a commit tree ID."""
        return self._require_repo().commit(expression).tree.hexsha

    def changed_paths(self, diff_ref: str) -> list[Path]:
        """Return paths changed from diff_ref relative to the repository root."""
        return [self.root / line for line in self._require_repo().git.diff("--name-only", diff_ref).splitlines()]

    def has_refs(self) -> bool:
        """Return whether the repository has at least one reference."""
        return any(self._require_repo().references)

    def has_ref(self, ref: str) -> bool:
        """Return whether the exact reference name exists."""
        return any(reference.path == ref for reference in self._require_repo().references)

    def merge_bases(self, left: str, right: str) -> tuple[str, ...]:
        """Return common ancestors of two commits."""
        return tuple(commit.hexsha for commit in self._require_repo().merge_base(left, right))

    def is_ancestor(self, ancestor: str, commit: str) -> bool:
        """Return whether ancestor is contained in commit."""
        repo = self._require_repo()
        ancestor_id = repo.commit(ancestor).hexsha
        return any(base.hexsha == ancestor_id for base in repo.merge_base(ancestor, commit))

    def common_dir(self) -> Path:
        """Return Git common directory for this repository."""
        return Path(self._require_repo().git.rev_parse("--git-common-dir"))

    def switch(self, branch: str, start: str, *, discard_changes: bool = False) -> None:
        """Switch an existing worktree branch to a commit."""
        arguments: list[str] = []
        if discard_changes:
            arguments.append("--discard-changes")
        arguments.extend(["-C", branch, start])
        self._require_repo().git.switch(*arguments)

    def reset_hard(self, commit: str) -> None:
        """Reset the current worktree to commit."""
        self._require_repo().git.reset("--hard", commit)

    def clean(self) -> None:
        """Remove untracked files from the current worktree."""
        self._require_repo().git.clean("-fdx")


def repository(path: Path) -> GitRepository:
    """Open a repository through the shared facade."""
    return GitRepository(path)


def native_git_result(
    *arguments: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    binary: bool = False,
) -> GitResult:
    """Run native Git porcelain, preserving binary NUL delimiters and filenames."""
    try:
        execute = cast(Any, Git(working_dir=str(cwd) if cwd else None).execute)
        return execute(
            ["git", *arguments],
            with_extended_output=True,
            with_exceptions=False,
            env=env,
            universal_newlines=not binary,
            stdout_as_string=not binary,
        )
    except (GitCommandNotFound, GitError, OSError) as error:
        message = f"Git command failed ({' '.join(arguments)}): {error}"
        raise RuntimeError(message) from error
