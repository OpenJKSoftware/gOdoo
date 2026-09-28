"""Resolve source selections into named Git branches and shared worktrees."""

import fcntl
import logging
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn, cast
from urllib.parse import quote, unquote, urlsplit

from git import Repo
from git.exc import GitCommandError, GitError
from git.refs import SymbolicReference
from git.repo.fun import find_worktree_git_dir
from gitdb import GitDB, LooseObjectDB

from ..git import GitRepository, GitUrl, native_git_result, repository
from ..helpers.hashing import fingerprint
from .specifications import GitMergeSource, GodooGitRepo
from .types import ResolvedSource, WorkspaceError

LOGGER = logging.getLogger(__name__)
MERGE_BEHAVIOR_VERSION = "ort-commit-tree-v1"
OBJECT_FILTER = "blob:none"
CONTAINER_ODOO = Path("/odoo/odoo")
CONTAINER_THIRDPARTY = Path("/odoo/thirdparty")


def workspace_fail(message: str) -> NoReturn:
    """Raise a workspace error while preserving the diagnostic."""
    raise WorkspaceError(message)


def _repo(path: Path | None = None) -> GitRepository:
    """Open the shared facade at path or the current directory."""
    return repository(path or Path.cwd())


def worktree_result(
    *arguments: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    binary: bool = False,
) -> tuple[int, str | bytes, str | bytes]:
    """Run worktree porcelain whose NUL metadata has no GitPython API."""
    return native_git_result(*arguments, cwd=cwd, env=env, binary=binary)


def _worktree_command(
    *arguments: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Run a worktree command and raise a workspace error on failure."""
    returncode, stdout, stderr = worktree_result(*arguments, cwd=cwd, env=env)
    if returncode:
        detail = stderr.strip() or stdout.strip()
        workspace_fail(f"Git worktree command failed ({' '.join(arguments)}): {detail}")
    return str(stdout).strip()


def _status_result(
    *arguments: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str | bytes, str | bytes]:
    """Run ignored status in porcelain NUL mode for the clean-tree safety check."""
    return native_git_result(*arguments, cwd=cwd, env=env, binary=True)


def _check_ref_format(*arguments: str) -> str:
    """Validate a branch or ref name with Git's native checker."""
    command = ("check-ref-format", *arguments)
    returncode, stdout, stderr = native_git_result(*command)
    if returncode:
        detail = stderr.strip() or stdout.strip()
        workspace_fail(f"Git command failed ({' '.join(command)}): {detail}")
    return str(stdout).strip()


def _fetch_without_fetch_head(
    pool: Path,
    refspec: str,
    *,
    remote: str = "origin",
    depth: int | None = None,
    unshallow: bool = False,
    object_filter: str = OBJECT_FILTER,
) -> None:
    """Fetch one ref without FETCH_HEAD, preserving gOdoo's lock-safe behavior."""
    if depth is not None and unshallow:
        message = "A Git fetch cannot set both depth and unshallow."
        raise ValueError(message)
    history_options = ["--unshallow"] if unshallow else ([f"--depth={depth}"] if depth is not None else [])
    filter_options = [f"--filter={object_filter}"] if object_filter else []
    arguments = (
        "-C",
        str(pool),
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        *history_options,
        *filter_options,
        "--",
        remote,
        refspec,
    )
    returncode, stdout, stderr = native_git_result(*arguments)
    if returncode:
        detail = stderr.strip() or stdout.strip()
        workspace_fail(f"Git fetch failed ({' '.join(arguments)}): {detail}")


def _merge_result(
    *arguments: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str | bytes, str | bytes]:
    """Run merge transaction commands that need their native status semantics."""
    return native_git_result(*arguments, cwd=cwd, env=env)


def _merge_command(
    *arguments: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Run a merge transaction command and preserve its diagnostic."""
    returncode, stdout, stderr = _merge_result(*arguments, cwd=cwd, env=env)
    if returncode:
        detail = stderr.strip() or stdout.strip()
        workspace_fail(f"Git merge command failed ({' '.join(arguments)}): {detail}")
    return str(stdout).strip()


def validate_prefix(prefix: str) -> str:
    """Reject a third-party namespace that could alter host or container depth."""
    from urllib.parse import unquote

    decoded = unquote(prefix)
    if (
        not prefix
        or decoded != prefix
        or decoded in {".", ".."}
        or "/" in decoded
        or "\\" in decoded
        or (len(decoded) > 1 and decoded[1] == ":")
    ):
        workspace_fail(f"Unsafe third-party manifest prefix: {prefix!r}")
    if len(Path(decoded).parts) != 1:
        workspace_fail(f"Unsafe third-party manifest prefix: {prefix!r}")
    return decoded


def repository_root(root: Path, url: str, *, role: str = "", prefix: str = "", name: str = "") -> Path:
    """Return the repository directory independently of manifest aliases."""
    if role == "odoo":
        return root
    if role == "addon" and prefix and name:
        return root / f"{validate_prefix(prefix)}_{name}"
    canonical = GitUrl(url).canonical
    if canonical.startswith("file://"):
        parts = Path(unquote(urlsplit(canonical).path)).parts[1:]
        host = "file"
    else:
        scp = re.fullmatch(r"(?:(?P<user>[^/@:]+)@)?(?P<host>[^/:]+):(?P<path>.+)", canonical)
        if scp and "://" not in canonical:
            host = scp["host"]
            parts = tuple(part for part in scp["path"].split("/") if part)
        else:
            parsed = urlsplit(canonical)
            host = parsed.hostname or ""
            if parsed.port is not None:
                host = f"{host}--port-{parsed.port}"
            parts = tuple(part for part in parsed.path.split("/") if part)
    if not parts:
        workspace_fail(f"Repository URL has no repository name: {canonical}")
    return root / _path_component(host) / Path(*(_path_component(part) for part in parts))


def _path_component(value: str) -> str:
    """Encode one URL-derived path component without permitting traversal."""
    encoded = quote(value, safe="._-")
    return encoded if encoded not in {"", ".", ".."} else "repository"


def managed_pool(repository_root: Path, url: str | None = None) -> bool:
    """Return whether a repository directory is a complete gOdoo-managed pool."""
    pool = repository_root / ".git"
    lock_path = repository_root / ".godoo.lock"
    try:
        if any(path.is_symlink() for path in (repository_root, pool, lock_path)):
            return False
        if not repository_root.is_dir() or not pool.is_dir() or not lock_path.is_file():
            return False
        origin = GitUrl(_repo(pool).remote_get_url("origin")).canonical
        return _repo(pool).is_bare() and (url is None or origin == GitUrl(url).canonical)
    except (OSError, WorkspaceError):
        return False


@contextmanager
def repository_lock(repository_root: Path, url: str | None = None, *, write: bool = True) -> Iterator[None]:
    """Serialize access to one managed repository pool and its worktrees."""
    lock_path = repository_root / ".godoo.lock"
    for path in (repository_root, *repository_root.parents, repository_root / ".git", lock_path):
        if path.is_symlink():
            workspace_fail(f"Managed repository paths must not be symlinks: {path}")
    if not repository_root.exists():
        if not write:
            workspace_fail(f"Managed repository is missing; run workspace sync: {repository_root}")
        repository_root.mkdir(parents=True, exist_ok=True)
    elif write and not lock_path.exists() and any(repository_root.iterdir()):
        if not managed_pool(repository_root, url):
            workspace_fail(f"Refusing to mark an existing repository as managed: {repository_root}")
    if not write and not lock_path.is_file():
        if managed_pool(repository_root, url):
            yield
            return
        workspace_fail(f"Managed repository lock is missing; run workspace sync: {repository_root}")
    flags = os.O_RDWR | os.O_CREAT if write else os.O_RDONLY
    fd = os.open(lock_path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        try:
            if lock_path.exists() and (repository_root / ".git").exists() and not managed_pool(repository_root, url):
                workspace_fail(f"Managed repository ownership validation failed: {repository_root}")
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def ensure_git_supports_relative_worktrees() -> None:
    """Probe Git in a temporary repository owned by the current process."""
    with tempfile.TemporaryDirectory(prefix="godoo-git-probe-") as temporary:
        pool = Path(temporary)
        GitRepository.init_bare(pool)
        _returncode, stdout, stderr = worktree_result("-C", str(pool), "worktree", "add", "-h")
    output = f"{stdout}{stderr}"
    if "relative-paths" not in output:
        workspace_fail(
            f"Git lacks worktree --relative-paths support; upgrade Git before syncing. Git reported: {output.strip()}"
        )


def _ensure_pool(repository_root: Path, url: str) -> Path:
    """Create or validate the bare repository that owns its linked worktrees."""
    identity = GitUrl(url).canonical
    pool = repository_root / ".git"
    lock_path = repository_root / ".godoo.lock"
    if lock_path.is_symlink() or (lock_path.exists() and not lock_path.is_file()):
        workspace_fail(f"Managed repository lock is not a regular file: {lock_path}")
    repository_root.mkdir(parents=True, exist_ok=True)
    if pool.exists():
        if pool.is_symlink() or not pool.is_dir() or not _repo(pool).is_bare():
            workspace_fail(f"Expected repository pool is not a bare Git repository: {pool}")
        configured_url = _repo(pool).remote_get_url("origin")
        if GitUrl(configured_url).canonical != identity:
            workspace_fail(f"Managed repository URL mismatch for {pool}: expected {identity}.")
    elif any(path != lock_path for path in repository_root.iterdir()):
        workspace_fail(f"Refusing to mark an existing repository as managed: {repository_root}")
    if not lock_path.exists():
        lock_path.touch(exist_ok=False)
    pool.mkdir(parents=True, exist_ok=True)
    if not (pool / "HEAD").exists():
        GitRepository.init_bare(pool)
        _repo(pool).remote_add("origin", GitUrl(url).transport)
    if not _repo(pool).is_bare():
        workspace_fail(f"Managed repository is not bare: {pool}")
    configured_url = _repo(pool).remote_get_url("origin")
    if GitUrl(configured_url).canonical != identity:
        workspace_fail(f"Managed repository URL mismatch for {pool}: expected {identity}.")
    return pool


def _promisor_remote(pool: Path, url: str) -> str:
    """Return a configured remote that can lazily supply promised objects."""
    canonical_url = GitUrl(url).canonical
    origin_url = _repo(pool).remote_get_url("origin")
    remote_name = (
        "origin" if GitUrl(origin_url).canonical == canonical_url else f"godoo-{fingerprint(canonical_url, 16)}"
    )
    returncode, stdout, stderr = native_git_result("-C", str(pool), "remote", "get-url", remote_name)
    if returncode:
        _merge_command("-C", str(pool), "remote", "add", remote_name, GitUrl(url).transport)
    elif GitUrl(str(stdout).strip()).canonical != canonical_url:
        detail = stderr.strip() or stdout.strip()
        workspace_fail(f"Managed promisor remote URL mismatch for {remote_name}: {detail}")
    _merge_command("-C", str(pool), "config", f"remote.{remote_name}.promisor", "true")
    _merge_command("-C", str(pool), "config", f"remote.{remote_name}.partialclonefilter", "blob:none")
    _merge_command("-C", str(pool), "config", "extensions.partialClone", "origin")
    return remote_name


def _git_fetch(
    pool: Path,
    refspec: str,
    *,
    remote: str = "origin",
    depth: int | None = None,
    shallow: bool = False,
    object_filter: str = OBJECT_FILTER,
) -> None:
    """Fetch one selected ref with the requested history policy."""
    pool_repo = _repo(pool)
    unshallow = depth is None and not shallow and pool_repo.is_shallow()
    if depth is None and shallow and (not pool_repo.has_refs() or pool_repo.is_shallow()):
        depth = 1
    history = (
        f" at depth {depth}"
        if depth is not None
        else (" while completing shallow history" if unshallow else " with full history")
    )
    LOGGER.info(
        "Fetching selected source ref %s from %s%s",
        refspec,
        remote,
        history,
    )
    _fetch_without_fetch_head(
        pool,
        refspec,
        remote=remote,
        depth=depth,
        unshallow=unshallow,
        object_filter=object_filter,
    )


def _resolve_selected_ref(
    pool: Path,
    url: str,
    branch: str,
    commit: str,
    *,
    shallow: bool,
    object_filter: str = OBJECT_FILTER,
) -> str:
    """Resolve commits and ordinary remote-tracking refs using Git's own locks."""
    _check_ref_format(f"refs/heads/{branch}")
    remote_name = f"godoo-{fingerprint(GitUrl(url).canonical, 16)}"
    remote_ref = f"refs/remotes/{remote_name}/{branch}"
    fetch_remote = _promisor_remote(pool, url) if object_filter else GitUrl(url).transport
    if commit:
        if not shallow and _repo(pool).is_shallow():
            _git_fetch(
                pool,
                f"+refs/heads/{branch}:{remote_ref}",
                remote=fetch_remote,
                shallow=False,
                object_filter=object_filter,
            )
        try:
            return _repo(pool).resolve(f"{commit}^{{commit}}")
        except (WorkspaceError, GitCommandError):
            try:
                _git_fetch(pool, commit, remote=fetch_remote, shallow=shallow, object_filter=object_filter)
            except WorkspaceError:
                workspace_fail(f"Pinned commit '{commit}' could not be resolved after fetching the configured remote.")
            try:
                return _repo(pool).resolve(f"{commit}^{{commit}}")
            except (WorkspaceError, GitCommandError):
                workspace_fail(f"Pinned commit '{commit}' could not be resolved after fetching the configured remote.")
    _git_fetch(
        pool,
        f"+refs/heads/{branch}:{remote_ref}",
        remote=fetch_remote,
        shallow=shallow,
        object_filter=object_filter,
    )
    return _repo(pool).resolve(f"{remote_ref}^{{commit}}")


def _recipe(merge_from: list[GitMergeSource]) -> tuple[dict[str, str], ...]:
    """Return credential-free manifest metadata for the ordered merge inputs."""
    return tuple(
        {"url": GitUrl(source.url).canonical, "branch": source.branch or "", "commit": source.commit or ""}
        for source in merge_from
    )


def encoded_path_component(value: str) -> str:
    """Encode a manifest value for use in a path component."""
    return quote(value, safe="._-")


def _recipe_fingerprint(repo: GodooGitRepo, branch: str) -> str:
    """Identify a merge recipe from manifest inputs rather than resolved refs."""
    recipe = {
        "base": {"url": GitUrl(repo.url).canonical, "branch": branch, "commit": repo.commit or ""},
        "merges": list(_recipe(repo.merge_from)),
        "behavior": MERGE_BEHAVIOR_VERSION,
    }
    return fingerprint(recipe)


def _selection_branch(
    repo: GodooGitRepo,
    *,
    branch: str,
    base_commit: str,
    merge_commits: tuple[str, ...],
) -> str:
    """Name the stable managed branch for one manifest selection."""
    namespace = repo.worktree_branch or "godoo"
    if merge_commits or repo.merge_from:
        key = _recipe_fingerprint(repo, branch)
        selected = f"{namespace}/mf-{encoded_path_component(repo.name)}-{encoded_path_component(branch)}-{key}"
    elif repo.commit:
        selected = f"{namespace}/lock-{base_commit}"
    else:
        selected = f"{namespace}/{encoded_path_component(branch)}"
    return selected


def _checkout_path(
    repository_root: Path,
    repo: GodooGitRepo,
    *,
    branch: str,
    base_commit: str,
    merge_commits: tuple[str, ...],
) -> Path:
    """Return the readable stable path for one manifest source selection."""
    if merge_commits or repo.merge_from:
        return (
            repository_root
            / f"merge-{encoded_path_component(repo.name)}-{encoded_path_component(branch)}-{_recipe_fingerprint(repo, branch)[:12]}"
        )
    if repo.commit:
        return repository_root / f"lock-{base_commit}"
    return repository_root / encoded_path_component(branch)


def _head(path: Path) -> str:
    """Return the current HEAD commit for a worktree."""
    return _repo(path).head_commit()


def _assert_clean(path: Path) -> None:
    """Reject any direct change to a managed worktree."""
    returncode, stdout, stderr = _status_result(
        "--no-optional-locks",
        "-C",
        str(path),
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored",
    )
    if returncode:
        detail = stderr.strip() or stdout.strip()
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        workspace_fail(f"Git status failed for {path}: {detail}")
    entries = os.fsdecode(stdout).split("\0")
    unexpected = [
        entry
        for entry in entries
        if entry
        and not (
            entry.startswith("!! ")
            and (candidate := path / entry[3:]).name == ".DS_Store"
            and candidate.is_file()
            and not candidate.is_symlink()
        )
    ]
    if unexpected:
        workspace_fail(f"Managed source is dirty; direct edits are unsupported: {path}")


def _worktree_pool(path: Path) -> Path:
    """Resolve a worktree's native common Git directory."""
    common = _repo(path).common_dir()
    return (path / common).resolve() if not common.is_absolute() else common.resolve()


def _is_linked_worktree(pool: Path, path: Path) -> bool:
    """Return whether Git records the path as a worktree of the pool."""
    output = _worktree_command("-C", str(pool), "worktree", "list", "--porcelain", "-z")
    registered = {
        Path(record.removeprefix("worktree ")).resolve()
        for record in output.split("\0")
        if record.startswith("worktree ")
    }
    return path.resolve() in registered and _worktree_pool(path) == pool.resolve()


def _has_merge_base(pool: Path, left: str, right: str) -> bool:
    """Check ancestry without treating unrelated histories as a command error."""
    try:
        return bool(_repo(pool).merge_bases(left, right))
    except Exception as error:
        workspace_fail(f"Cannot determine source merge ancestry: {error}")


def _ensure_merge_history(
    pool: Path,
    inputs: tuple[tuple[str, str], ...],
    *,
    object_filter: str = OBJECT_FILTER,
) -> None:
    """Deepen selected histories until every merge has a common ancestor."""
    for depth in (64, 256, 1024, 2147483647):
        if all(
            any(_has_merge_base(pool, previous, commit) for _previous_url, previous in inputs[:index])
            for index, (_url, commit) in enumerate(inputs[1:], 1)
        ):
            return
        if not _repo(pool).is_shallow():
            break
        for url, commit in inputs:
            fetch_remote = _promisor_remote(pool, url) if object_filter else GitUrl(url).transport
            _git_fetch(pool, commit, remote=fetch_remote, depth=depth, object_filter=object_filter)
    for index, (_url, commit) in enumerate(inputs[1:], 1):
        if not any(_has_merge_base(pool, previous, commit) for _previous_url, previous in inputs[:index]):
            workspace_fail(
                f"Source merge {commit} has no common ancestor with earlier selected inputs; unrelated histories are unsupported."
            )


def _checkout_environment() -> dict[str, str]:
    """Exclude host Git settings from checkout and merge behavior."""
    return {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "0",
        "GIT_AUTHOR_NAME": "gOdoo",
        "GIT_AUTHOR_EMAIL": "godoo@localhost",
        "GIT_COMMITTER_NAME": "gOdoo",
        "GIT_COMMITTER_EMAIL": "godoo@localhost",
    }


def _merge_commit(path: Path, tree: str, previous: str, commit: str) -> str:
    """Write the deterministic commit used for one ordered merge input."""
    environment = _checkout_environment()
    timestamp = max(_repo(path).commit_timestamp(parent) for parent in (previous, commit)) + 1
    environment.update(GIT_AUTHOR_DATE=f"{timestamp} +0000", GIT_COMMITTER_DATE=f"{timestamp} +0000")
    return _merge_command(
        "-C",
        str(path),
        "-c",
        "commit.gpgSign=false",
        "commit-tree",
        tree,
        "-p",
        previous,
        "-p",
        commit,
        "-m",
        f"gOdoo merge {commit}",
        env=environment,
    )


def _merge_sources(path: Path, merge_commits: tuple[str, ...]) -> str:
    """Merge ordered inputs using fixed configuration and reproducible commits."""
    environment = _checkout_environment()
    for commit in merge_commits:
        previous = _head(path)
        _merge_command(
            "-C",
            str(path),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.autocrlf=false",
            "-c",
            "merge.renormalize=false",
            "-c",
            "rerere.enabled=false",
            "merge",
            "--strategy=ort",
            "--no-ff",
            "--no-commit",
            "--no-stat",
            "--no-edit",
            "--",
            commit,
            env=environment,
        )
        merge_head = Path(_merge_command("-C", str(path), "rev-parse", "--git-path", "MERGE_HEAD"))
        if not merge_head.is_absolute():
            merge_head = path / merge_head
        if not merge_head.exists():
            continue
        tree = _merge_command("-C", str(path), "write-tree", env=environment)
        merged = _merge_commit(path, tree, previous, commit)
        _merge_command("-C", str(path), "update-ref", "HEAD", merged, previous)
        _merge_command("-C", str(path), "merge", "--quit", env=environment)
    return _head(path)


def registered_worktrees(pool: Path) -> list[dict[str, str]]:
    """Read branch ownership and paths from native Git worktree metadata."""
    output = _worktree_command("-C", str(pool), "worktree", "list", "--porcelain", "-z")
    worktrees: list[dict[str, str]] = []
    for record in output.split("\0\0"):
        if not record:
            continue
        fields: dict[str, str] = {}
        for field in record.split("\0"):
            if not field:
                continue
            key, separator, value = field.partition(" ")
            fields[key] = value if separator else ""
        worktrees.append(fields)
    return worktrees


def _usable_worktree(pool: Path, path: Path, root: Path) -> None:
    """Validate a managed worktree before allowing gOdoo to reset it."""
    if (
        any(parent.is_symlink() for parent in (path, *path.parents, path / ".git"))
        or not path.is_dir()
        or not path.resolve().is_relative_to(root.resolve())
    ):
        workspace_fail(f"Registered source worktree is missing or outside the source root: {path}")
    if any(part.startswith(".godoo-worktree-") for part in path.parts):
        workspace_fail(f"Source branch has an unfinished worktree; retry after its preparation completes: {path}")
    if not _is_linked_worktree(pool, path):
        workspace_fail(f"Refusing to adopt unmanaged checkout: {path}")


def _publish_worktree(
    *,
    pool: Path,
    path: Path,
    root: Path,
    worktree_branch: str,
    base_commit: str,
    merge_commits: tuple[str, ...],
) -> tuple[Path, str]:
    """Reset a stable managed branch or create it when this is its first sync."""
    # Reuse only a registered worktree; an arbitrary directory cannot become managed state.
    branch_ref = f"refs/heads/{worktree_branch}"
    records = registered_worktrees(pool)
    record = next((record for record in records if record.get("branch") == branch_ref), None)
    if record is None:
        record = next((record for record in records if Path(record["worktree"]) == path), None)
    if record is not None:
        selected = Path(record["worktree"])
        _usable_worktree(pool, selected, root)
        if selected != path:
            occupant = next((item for item in records if Path(item.get("worktree", "")) == path), None)
            if occupant is not None:
                _usable_worktree(pool, path, root)
                _assert_clean(path)
                _worktree_command("-C", str(pool), "worktree", "remove", str(path))
            elif path.exists() or path.is_symlink():
                workspace_fail(f"Readable source worktree path is occupied: {path}")
            _assert_clean(selected)
            path.parent.mkdir(parents=True, exist_ok=True)
            _worktree_command("-C", str(pool), "worktree", "move", str(selected), str(path))
            selected = path
        if record.get("branch") and record.get("branch") != branch_ref and selected == path:
            workspace_fail(
                f"Managed worktree path is occupied by another branch: {path} ({record.get('branch', 'detached')})."
            )
        previous = _head(selected)
        if record.get("branch") != branch_ref:
            _repo(selected).switch(worktree_branch, base_commit, discard_changes=True)
        _repo(selected).reset_hard(base_commit)
        _repo(selected).clean()
        try:
            resolved = _merge_sources(selected, merge_commits) if merge_commits else _head(selected)
        except Exception:
            _repo(selected).reset_hard(previous)
            _repo(selected).clean()
            raise
        return selected, resolved

    # ``for-each-ref`` also matches descendants (for example an older
    # ``godoo/19.0/<commit>`` ref), which must not make this exact branch look
    # existing to ``worktree add``.
    existing = _repo(pool).has_ref(branch_ref)
    return _create_worktree(
        pool=pool,
        path=path,
        worktree_branch=worktree_branch,
        base_commit=base_commit,
        merge_commits=merge_commits,
        existing=bool(existing),
    )


def _create_worktree(
    *,
    pool: Path,
    path: Path,
    worktree_branch: str,
    base_commit: str,
    merge_commits: tuple[str, ...],
    existing: bool,
) -> tuple[Path, str]:
    """Prepare a named worktree and remove only its own staged state if preparation fails."""
    branch_ref = f"refs/heads/{worktree_branch}"
    if path.exists() or path.is_symlink():
        workspace_fail(f"Source worktree path is already occupied; inspect it with git worktree list: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Stage checkout outside the final path so failures leave no managed worktree behind.
    with tempfile.TemporaryDirectory(prefix=".godoo-worktree-", dir=path.parent) as temporary:
        staging = Path(temporary) / "worktree"
        created_branch = False
        published = False
        try:
            branch_options = [] if existing else ["-b", worktree_branch]
            _worktree_command(
                "-C",
                str(pool),
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "add",
                "--relative-paths",
                *branch_options,
                str(staging),
                worktree_branch if existing else base_commit,
                env=_checkout_environment(),
            )
            created_branch = not existing
            if existing:
                _repo(staging).reset_hard(base_commit)
                _repo(staging).clean()
            resolved = _merge_sources(staging, merge_commits) if merge_commits else _head(staging)
            _assert_clean(staging)
            _worktree_command(
                "-C", str(pool), "-c", "worktree.useRelativePaths=true", "worktree", "move", str(staging), str(path)
            )
            published = True
            return path, resolved
        finally:
            if not published and staging.exists():
                abandoned = _head(staging)
                _worktree_command("-C", str(pool), "worktree", "remove", "--force", str(staging))
                if created_branch:
                    _merge_command("-C", str(pool), "update-ref", "-d", branch_ref, abandoned)


def _resolved_source(
    repo: GodooGitRepo,
    *,
    branch: str,
    worktree_branch: str,
    base_commit: str,
    resolved_commit: str,
    merge_commits: tuple[str, ...],
    role: str,
    prefix: str,
    host_path: Path,
) -> ResolvedSource:
    """Build the source record shared by synchronization and inspection."""
    if role == "addon":
        prefix = validate_prefix(prefix)
    return ResolvedSource(
        role=role,
        prefix=prefix,
        name=repo.name,
        url=GitUrl(repo.url).canonical,
        branch=branch,
        worktree_branch=worktree_branch,
        requested_commit=repo.commit or "",
        base_commit=base_commit,
        resolved_commit=resolved_commit,
        merge_from=_recipe(repo.merge_from),
        merge_commits=merge_commits,
        recipe_fingerprint=_recipe_fingerprint(repo, branch) if repo.merge_from else "",
        host_path=host_path,
        container_path=(CONTAINER_ODOO if role == "odoo" else _container_addon_path(prefix, repo.name)),
    )


def _container_addon_path(prefix: str, name: str) -> Path:
    """Build and confine the third-party container target."""
    target = CONTAINER_THIRDPARTY / f"{validate_prefix(prefix)}_{name}"
    if not target.is_relative_to(CONTAINER_THIRDPARTY):
        workspace_fail(f"Managed addon container path escaped /odoo/thirdparty: {target}")
    return target


def sync_repo_unlocked(
    repo: GodooGitRepo,
    *,
    root: Path,
    default_branch: str,
    role: str,
    prefix: str = "",
) -> ResolvedSource:
    """Resolve and update one stable source worktree."""
    if role == "addon":
        prefix = validate_prefix(prefix)
    branch = repo.branch or default_branch
    if repo.worktree_branch:
        _check_ref_format("--branch", repo.worktree_branch)
    # Resolve all requested revisions in the pool before publishing a worktree to callers.
    pool_root = repository_root(root, repo.url, role=role, prefix=prefix, name=repo.name)
    pool = _ensure_pool(pool_root, repo.url)
    base_commit = _resolve_selected_ref(
        pool,
        repo.url,
        branch,
        repo.commit or "",
        shallow=repo.shallow,
        object_filter=OBJECT_FILTER,
    )
    merge_commits = tuple(
        _resolve_selected_ref(
            pool,
            source.url,
            source.branch or branch,
            source.commit or "",
            shallow=repo.shallow,
            object_filter=OBJECT_FILTER,
        )
        for source in repo.merge_from
    )
    worktree_branch = _selection_branch(repo, branch=branch, base_commit=base_commit, merge_commits=merge_commits)
    _check_ref_format("--branch", worktree_branch)
    if merge_commits:
        _ensure_merge_history(
            pool,
            (
                (repo.url, base_commit),
                *tuple(zip((source.url for source in repo.merge_from), merge_commits, strict=True)),
            ),
            object_filter=OBJECT_FILTER,
        )
    # Publish only after merge ancestry is complete, so the selected path names a reproducible tree.
    path, resolved_commit = _publish_worktree(
        pool=pool,
        path=_checkout_path(
            pool_root,
            repo,
            branch=branch,
            base_commit=base_commit,
            merge_commits=merge_commits,
        ),
        root=root,
        worktree_branch=worktree_branch,
        base_commit=base_commit,
        merge_commits=merge_commits,
    )
    if not path.resolve().is_relative_to(root.resolve()):
        workspace_fail(f"Managed source path escaped its host root: {path}")
    return _resolved_source(
        repo,
        branch=branch,
        worktree_branch=worktree_branch,
        base_commit=base_commit,
        resolved_commit=resolved_commit,
        merge_commits=merge_commits,
        host_path=path,
        role=role,
        prefix=prefix,
    )


def inspect_repo_unlocked(
    repo: GodooGitRepo,
    *,
    root: Path,
    default_branch: str,
    role: str,
    prefix: str = "",
) -> ResolvedSource:
    """Read one managed source without fetching, creating, or changing files."""
    if role == "addon":
        prefix = validate_prefix(prefix)
    branch = repo.branch or default_branch
    # Inspect the existing pool only; this path must not fetch, create, or repair source state.
    pool_root = repository_root(root, repo.url, role=role, prefix=prefix, name=repo.name)
    pool = pool_root / ".git"
    if not pool.is_dir():
        workspace_fail(f"Managed repository is missing; run workspace sync: {pool_root}")
    configured_url = _repo(pool).remote_get_url("origin")
    if GitUrl(configured_url).canonical != GitUrl(repo.url).canonical:
        workspace_fail(f"Managed repository URL mismatch for {pool_root}.")
    base_commit = _inspect_ref(pool, repo.url, branch, repo.commit or "")
    merge_commits = tuple(
        _inspect_ref(pool, source.url, source.branch or branch, source.commit or "") for source in repo.merge_from
    )
    expected_branch = _selection_branch(repo, branch=branch, base_commit=base_commit, merge_commits=merge_commits)
    _check_ref_format("--branch", expected_branch)
    record = next(
        (item for item in registered_worktrees(pool) if item.get("branch") == f"refs/heads/{expected_branch}"),
        None,
    )
    if record is None:
        workspace_fail(f"Managed source worktree is missing; run workspace sync: {pool_root}")
    path = Path(record["worktree"])
    _usable_worktree(pool, path, root)
    _assert_clean(path)
    resolved = _head(path)
    if repo.merge_from:
        if not _matches_merge_result(pool, resolved, base_commit, merge_commits):
            workspace_fail(f"Managed merge source differs from its configured recipe: {path}")
    elif resolved != base_commit:
        workspace_fail(f"Managed source differs from its configured ref: {path}")
    return _resolved_source(
        repo,
        branch=branch,
        worktree_branch=expected_branch,
        base_commit=base_commit,
        resolved_commit=resolved,
        merge_commits=merge_commits,
        role=role,
        prefix=prefix,
        host_path=path,
    )


def select_repo_unlocked(
    repo: GodooGitRepo,
    *,
    root: Path,
    default_branch: str,
    role: str,
    prefix: str = "",
) -> ResolvedSource:
    """Select an existing worktree from local Git metadata without Git commands."""
    if role == "addon":
        prefix = validate_prefix(prefix)
    branch = repo.branch or default_branch
    pool_root = repository_root(root, repo.url, role=role, prefix=prefix, name=repo.name)
    pool = pool_root / ".git"
    if not pool.is_dir() or pool.is_symlink():
        workspace_fail(f"Managed repository is missing; run workspace sync: {pool_root}")
    try:
        configured_url = _repo(pool).remote_get_url("origin")
    except (OSError, RuntimeError, GitError) as error:
        workspace_fail(f"Unable to read managed repository metadata at {pool_root}: {error}")
    if GitUrl(configured_url).canonical != GitUrl(repo.url).canonical:
        workspace_fail(f"Managed repository URL mismatch for {pool_root}.")

    base_commit = _metadata_ref(pool, repo.url, branch, repo.commit or "")
    merge_commits = tuple(
        _metadata_ref(pool, source.url, source.branch or branch, source.commit or "") for source in repo.merge_from
    )
    worktree_branch = _selection_branch(
        repo,
        branch=branch,
        base_commit=base_commit,
        merge_commits=merge_commits,
    )
    branch_ref = f"refs/heads/{worktree_branch}"
    selected_commit = _metadata_commit(pool, branch_ref, pool_root=pool_root)
    path = _checkout_path(
        pool_root,
        repo,
        branch=branch,
        base_commit=base_commit,
        merge_commits=merge_commits,
    )
    if path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(root.resolve()):
        workspace_fail(f"Managed source worktree is missing or outside its host root; run workspace sync: {path}")
    head_commit = _metadata_worktree_head(pool, path, worktree_branch)
    if head_commit != selected_commit:
        workspace_fail(f"Managed source worktree HEAD does not match its selected branch: {path}")
    if not repo.merge_from and head_commit != base_commit:
        workspace_fail(f"Managed source differs from its configured ref: {path}")
    return _resolved_source(
        repo,
        branch=branch,
        worktree_branch=worktree_branch,
        base_commit=base_commit,
        resolved_commit=head_commit,
        merge_commits=merge_commits,
        host_path=path,
        role=role,
        prefix=prefix,
    )


def _metadata_ref(pool: Path, url: str, branch: str, commit: str) -> str:
    """Resolve a pin or tracking ref from the local object database."""
    if commit:
        expression = f"{commit}^{{commit}}"
    else:
        remote_name = f"godoo-{fingerprint(GitUrl(url).canonical, 16)}"
        expression = f"refs/remotes/{remote_name}/{branch}^{{commit}}"
    return _metadata_commit(pool, expression, pool_root=pool.parent)


def _metadata_commit(pool: Path, expression: str, *, pool_root: Path) -> str:
    """Resolve one existing commit without starting a Git process."""
    try:
        return _repo(pool).metadata_resolve(expression)
    except (GitError, OSError, RuntimeError, ValueError) as error:
        workspace_fail(f"Selected commit is missing from managed repository; run workspace sync: {pool_root}: {error}")


def _metadata_worktree_head(pool: Path, path: Path, branch: str) -> str:
    """Read a linked worktree HEAD through GitPython's symbolic reference API."""
    gitdir = find_worktree_git_dir(path / ".git")
    if gitdir is None:
        workspace_fail(f"Managed source worktree has no valid Git directory pointer: {path}")
    metadata = (path / gitdir).resolve()
    common_dir = pool.resolve()
    if metadata.parent != common_dir / "worktrees":
        workspace_fail(f"Managed source Git metadata is outside its pool: {metadata}")
    try:
        metadata_repo = Repo(common_dir, odbt=cast(type[LooseObjectDB], GitDB))
        head = SymbolicReference(metadata_repo, f"{metadata.relative_to(common_dir)}/HEAD")
        head_ref = head.ref.path
        head_commit = head.commit.hexsha
    except (GitError, OSError, RuntimeError, ValueError) as error:
        workspace_fail(f"Unable to read managed source worktree HEAD at {path}: {error}")
    if head_ref != f"refs/heads/{branch}":
        workspace_fail(f"Managed source worktree is on an unexpected branch: {path}")
    return head_commit


def _inspect_ref(pool: Path, url: str, branch: str, commit: str) -> str:
    """Resolve an already fetched source ref without contacting its remote."""
    if commit:
        return _repo(pool).resolve(f"{commit}^{{commit}}")
    remote_name = f"godoo-{fingerprint(GitUrl(url).canonical, 16)}"
    return _repo(pool).resolve(f"refs/remotes/{remote_name}/{branch}^{{commit}}")


def _is_ancestor(pool: Path, ancestor: str, commit: str) -> bool:
    """Return whether one cached commit is already contained in another."""
    try:
        return _repo(pool).is_ancestor(ancestor, commit)
    except Exception as error:
        workspace_fail(f"Cannot verify source merge ancestry: {error}")


def _matches_merge_result(pool: Path, resolved: str, base: str, merges: tuple[str, ...]) -> bool:
    """Replay an ordered recipe in temporary objects and compare its final tree.

    Only author and committer metadata may differ. Ordered parents, the generated
    subject, and the tree must match. Git's ``merge-tree`` uses the same ort
    strategy while the alternate object directory keeps verification from
    writing objects into the shared pool.

    Returns:
        Whether the published merge chain matches the configured recipe.
    """
    # Replay in temporary object storage so verification cannot alter the managed pool.
    with tempfile.TemporaryDirectory(prefix="godoo-merge-verify-") as temporary:
        objects = Path(temporary) / "objects"
        objects.mkdir()
        environment = _checkout_environment()
        environment.update(
            GIT_OBJECT_DIRECTORY=str(objects),
            GIT_ALTERNATE_OBJECT_DIRECTORIES=str(pool / "objects"),
        )
        chain: list[tuple[str, str, str]] = []
        candidate = resolved
        while candidate != base:
            if len(chain) >= len(merges):
                return False
            fields = [
                " ".join(_repo(pool).commit_parents(candidate)),
                _repo(pool).commit_subject(candidate),
            ]
            parents = fields[0].split() if fields else []
            if len(parents) != 2 or len(fields) != 2 or fields[1] != f"gOdoo merge {parents[1]}":
                return False
            chain.append((parents[0], parents[1], candidate))
            candidate = parents[0]
        steps = iter(reversed(chain))
        current = base
        for merge in merges:
            if _is_ancestor(pool, merge, current):
                continue
            step = next(steps, None)
            if step is None or step[:2] != (current, merge):
                return False
            returncode, stdout, stderr = _merge_result(
                "-C", str(pool), "merge-tree", "--write-tree", current, merge, env=environment
            )
            if returncode:
                LOGGER.debug("Unable to replay managed merge recipe: %s", str(stderr).strip())
                return False
            expected_tree = str(stdout).strip().splitlines()[0]
            if _repo(pool).tree(step[2]) != expected_tree:
                return False
            current = step[2]
        return current == resolved and next(steps, None) is None
