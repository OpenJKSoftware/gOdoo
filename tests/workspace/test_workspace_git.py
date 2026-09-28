"""Tests managed Git workspace synchronization and recovery behavior."""

import os
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

import godoo_cli.workspace.git as workspace_git
from godoo_cli.git import GitUrl, native_git_result, repository
from godoo_cli.git.repository import Git
from godoo_cli.models import GitMergeSource, GodooGitRepo
from godoo_cli.workspace.git import (
    _assert_clean,
    _check_ref_format,
    _ensure_pool,
    _worktree_pool,
    ensure_git_supports_relative_worktrees,
    inspect_repo_unlocked,
    repository_lock,
    repository_root,
    select_repo_unlocked,
    sync_repo_unlocked,
)
from godoo_cli.workspace.types import WorkspaceError


def _sync_repo(repo: GodooGitRepo, *, root: Path, default_branch: str, role: str, prefix: str = ""):
    """Exercise the unlocked sync path while owning its repository lock."""
    if repo.worktree_branch:
        _check_ref_format("--branch", repo.worktree_branch)
    pool_root = repository_root(root, repo.url, role=role, prefix=prefix, name=repo.name)
    with repository_lock(pool_root, repo.url):
        return sync_repo_unlocked(repo, root=root, default_branch=default_branch, role=role, prefix=prefix)


def _inspect_repo(repo: GodooGitRepo, *, root: Path, default_branch: str, role: str, prefix: str = ""):
    """Exercise the unlocked inspect path while owning its repository lock."""
    with repository_lock(
        repository_root(root, repo.url, role=role, prefix=prefix, name=repo.name), repo.url, write=False
    ):
        return inspect_repo_unlocked(repo, root=root, default_branch=default_branch, role=role, prefix=prefix)


def _git(path: Path, *args: str) -> str:
    return repository(path).run("-C", str(path), *args)


def _remote(tmp_path: Path) -> tuple[Path, Path, str]:
    working = tmp_path / "working"
    remote = tmp_path / "remote.git"
    working.mkdir(parents=True)
    _git(working, "init", "-b", "19.0")
    _git(working, "config", "user.name", "Test")
    _git(working, "config", "user.email", "test@example.test")
    (working / "shared.txt").write_text("original\n", encoding="utf-8")
    _git(working, "add", ".")
    _git(working, "commit", "-m", "initial")
    _git(working, "clone", "--bare", str(working), str(remote))
    return working, remote, _git(working, "rev-parse", "HEAD")


def _commit(working: Path, filename: str, content: str) -> str:
    (working / filename).write_text(content, encoding="utf-8")
    _git(working, "add", filename)
    _git(working, "commit", "-m", filename)
    return _git(working, "rev-parse", "HEAD")


def _selection(remote: Path, root: Path) -> tuple[str, str]:
    source = _sync_repo(GodooGitRepo(url=remote.as_uri(), branch="19.0"), root=root, default_branch="19.0", role="odoo")
    return str(source.host_path), source.resolved_commit


def _merge_spec(remote: Path) -> GodooGitRepo:
    return GodooGitRepo(
        url=remote.as_uri(), branch="19.0", merge_from=[GitMergeSource(url=remote.as_uri(), branch="feature")]
    )


def _selected_repo(repo: GodooGitRepo, *, root: Path, role: str = "odoo"):
    """Resolve existing selected source metadata without a repository lock."""
    return select_repo_unlocked(repo, root=root, default_branch="19.0", role=role)


@pytest.mark.parametrize("selection", ["branch", "pin", "merge"])
def test_fast_source_selection_uses_metadata_and_allows_live_edits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, selection: str
) -> None:
    """Select branch, pin, and merge worktrees without status scans or Git processes."""
    working, remote, initial = _remote(tmp_path)
    if selection == "branch":
        repo = GodooGitRepo(url=remote.as_uri(), branch="19.0")
    elif selection == "pin":
        repo = GodooGitRepo(url=remote.as_uri(), branch="19.0", commit=initial)
    else:
        _git(working, "checkout", "-b", "feature")
        _commit(working, "feature.txt", "feature\n")
        _git(working, "push", str(remote), "feature")
        _git(working, "checkout", "19.0")
        repo = _merge_spec(remote)

    root = tmp_path / "sources"
    synced = _sync_repo(repo, root=root, default_branch="19.0", role="odoo")
    (synced.host_path / "shared.txt").write_text("local edit\n", encoding="utf-8")

    def forbid_git_process(*_args: object, **_kwargs: object) -> None:
        message = "fast selection started a Git process"
        raise AssertionError(message)

    with monkeypatch.context() as patch:
        patch.setattr(Git, "execute", forbid_git_process)
        selected = _selected_repo(repo, root=root)

    assert selected.host_path == synced.host_path
    assert selected.base_commit == synced.base_commit
    assert selected.merge_commits == synced.merge_commits
    assert selected.resolved_commit == synced.resolved_commit
    assert (selected.host_path / "shared.txt").read_text(encoding="utf-8") == "local edit\n"
    with pytest.raises(WorkspaceError):
        _inspect_repo(repo, root=root, default_branch="19.0", role="odoo")


def test_fast_source_selection_relocates_with_relative_worktree_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A moved shared source root is resolved from its current project setting."""
    _working, remote, _initial = _remote(tmp_path)
    repo = GodooGitRepo(url=remote.as_uri(), branch="19.0")
    original_root = tmp_path / "sources"
    _sync_repo(repo, root=original_root, default_branch="19.0", role="odoo")
    moved_root = tmp_path / "relocated sources"
    original_root.rename(moved_root)

    def forbid_git_process(*_args: object, **_kwargs: object) -> None:
        message = "fast selection started a Git process"
        raise AssertionError(message)

    monkeypatch.setattr(Git, "execute", forbid_git_process)
    selected = _selected_repo(repo, root=moved_root)

    assert selected.host_path == moved_root / "19.0"


def test_git_adapter_retains_streams_exit_status_and_binary_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the contract that git adapter retains streams exit status and binary output."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def execute(_self: object, command: list[str], **kwargs: object) -> tuple[int, str | bytes, str | bytes]:
        calls.append((command, kwargs))
        if command[-1] == "failure":
            return 1, "stdout\n", "stderr\n"
        if kwargs["universal_newlines"]:
            return 0, "success\n", ""
        return 0, b"file-\xff\0", b""

    monkeypatch.setattr(Git, "execute", execute)

    assert native_git_result("status") == (0, "success\n", "")
    assert native_git_result("failure") == (1, "stdout\n", "stderr\n")
    assert native_git_result("ls-files", "-z", binary=True) == (0, b"file-\xff\0", b"")
    assert [command for command, _kwargs in calls] == [
        ["git", "status"],
        ["git", "failure"],
        ["git", "ls-files", "-z"],
    ]
    assert all(
        kwargs["with_extended_output"] is True and kwargs["with_exceptions"] is False for _command, kwargs in calls
    )
    assert calls[-1][1]["universal_newlines"] is False
    assert calls[-1][1]["stdout_as_string"] is False


def test_advancing_one_consumer_reuses_and_resets_floating_worktree(tmp_path: Path) -> None:
    """Guards the contract that advancing one consumer reuses and resets floating worktree."""
    working, remote, initial = _remote(tmp_path)
    root = tmp_path / "sources"
    first, initial_selected = _selection(remote, root)
    assert _selection(remote, root) == (first, initial_selected)
    advanced = _commit(working, "shared.txt", "advanced\n")
    _git(working, "push", str(remote), "19.0")

    second, advanced_selected = _selection(remote, root)

    assert first == second
    assert initial_selected == initial
    assert advanced_selected == advanced
    assert _git(Path(first), "rev-parse", "HEAD") == advanced
    assert (Path(first) / "shared.txt").read_text(encoding="utf-8") == "advanced\n"
    assert (Path(second) / "shared.txt").read_text(encoding="utf-8") == "advanced\n"
    _assert_old_generation_refs_are_retained(tmp_path / "old-generation")
    _assert_existing_floating_worktree_is_reset(tmp_path / "floating-reset")


def test_shallow_fetch_uses_depth_one_for_an_existing_shallow_pool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guards the contract that existing shallow pool fetches a new floating branch at depth one."""

    class ShallowPool:
        def has_refs(self) -> bool:
            return True

        def is_shallow(self) -> bool:
            return True

    captured: dict[str, object] = {}
    monkeypatch.setattr(workspace_git, "_repo", lambda _pool: ShallowPool())
    monkeypatch.setattr(
        workspace_git,
        "_fetch_without_fetch_head",
        lambda pool, refspec, **kwargs: captured.update(pool=pool, refspec=refspec, **kwargs),
    )

    workspace_git._git_fetch(
        tmp_path / "pool",
        "+refs/heads/next:refs/remotes/godoo-test/next",
        remote="https://example.test/odoo.git",
        shallow=True,
    )

    assert captured == {
        "pool": tmp_path / "pool",
        "refspec": "+refs/heads/next:refs/remotes/godoo-test/next",
        "remote": "https://example.test/odoo.git",
        "depth": 1,
        "unshallow": False,
        "object_filter": workspace_git.OBJECT_FILTER,
    }


def test_shallow_pool_keeps_default_and_secondary_floating_worktrees(tmp_path: Path) -> None:
    """Guards the contract that shallow pools keep default and secondary worktrees."""
    working, remote, _initial = _remote(tmp_path)
    _git(working, "checkout", "-b", "next")
    _commit(working, "version.txt", "next\n")
    _git(working, "push", str(remote), "next")
    _git(working, "checkout", "19.0")
    root = tmp_path / "sources"

    source_19 = _sync_repo(
        GodooGitRepo(url=str(remote), branch="19.0", shallow=True),
        root=root,
        default_branch="19.0",
        role="odoo",
    )
    pool = _worktree_pool(source_19.host_path)
    assert _git(pool, "rev-parse", "--is-shallow-repository") == "true"

    secondary_source = _sync_repo(
        GodooGitRepo(url=str(remote), branch="next", shallow=True),
        root=root,
        default_branch="19.0",
        role="odoo",
    )

    assert source_19.host_path == root / "19.0"
    assert secondary_source.host_path == root / "next"
    assert _git(pool, "rev-parse", "--is-shallow-repository") == "true"


def _assert_full_history_fetch_deepens_shallow_pool(tmp_path: Path) -> None:
    """Guards the default history policy and migration from an existing shallow pool."""
    working, remote, _initial = _remote(tmp_path)
    _commit(working, "next.txt", "next\n")
    _git(working, "push", str(remote), "19.0")
    root = tmp_path / "sources"

    shallow_source = _sync_repo(
        GodooGitRepo(url=str(remote), branch="19.0", shallow=True),
        root=root,
        default_branch="19.0",
        role="odoo",
    )
    pool = _worktree_pool(shallow_source.host_path)
    assert _git(pool, "rev-parse", "--is-shallow-repository") == "true"
    assert _git(shallow_source.host_path, "rev-list", "--count", "HEAD") == "1"

    full_source = _sync_repo(
        GodooGitRepo(url=str(remote), branch="19.0"),
        root=root,
        default_branch="19.0",
        role="odoo",
    )

    assert full_source.host_path == shallow_source.host_path
    assert _git(pool, "rev-parse", "--is-shallow-repository") == "false"
    assert _git(full_source.host_path, "rev-list", "--count", "HEAD") == "2"
    _assert_full_history_fetch_deepens_shallow_pool(tmp_path / "full-history")


def test_blobless_fetch_keeps_history_and_materializes_only_checkout_blobs(tmp_path: Path) -> None:
    """Guards full commit history without downloading blobs outside the checkout."""
    working, remote, _initial = _remote(tmp_path)
    historical_blob = _git(working, "rev-parse", "HEAD:shared.txt")
    _git(working, "rm", "shared.txt")
    (working / "current.txt").write_text("current\n", encoding="utf-8")
    _git(working, "add", "current.txt")
    _git(working, "commit", "-m", "replace historical file")
    _git(working, "push", str(remote), "19.0")
    _git(remote, "config", "uploadpack.allowFilter", "true")

    source = _sync_repo(
        GodooGitRepo(url=str(remote), branch="19.0"),
        root=tmp_path / "sources",
        default_branch="19.0",
        role="odoo",
    )
    pool = _worktree_pool(source.host_path)

    assert _git(pool, "config", "--get", "extensions.partialClone") == "origin"
    assert _git(pool, "config", "--get", "remote.origin.promisor") == "true"
    assert _git(pool, "config", "--get", "remote.origin.partialclonefilter") == "blob:none"
    assert _git(source.host_path, "rev-list", "--count", "HEAD") == "2"
    assert (source.host_path / "current.txt").read_text(encoding="utf-8") == "current\n"
    environment = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    result = subprocess.run(
        ["git", "-C", str(pool), "cat-file", "-e", historical_blob],
        check=False,
        capture_output=True,
        env=environment,
    )
    assert result.returncode != 0


def test_pinned_and_floating_inputs_use_stable_distinct_branches(tmp_path: Path) -> None:
    """Guards the contract that pinned and floating inputs use stable distinct branches."""
    _working, remote, initial = _remote(tmp_path)
    root = tmp_path / "sources"
    floating, _commit_id = _selection(remote, root)

    pinned = _sync_repo(
        GodooGitRepo(url=str(remote), branch="19.0", commit=initial),
        root=root,
        default_branch="19.0",
        role="odoo",
    )

    assert str(pinned.host_path) != floating
    assert pinned.worktree_branch == f"godoo/lock-{initial}"
    assert _git(pinned.host_path, "symbolic-ref", "--short", "HEAD") == pinned.worktree_branch


def _assert_old_generation_refs_are_retained(tmp_path: Path) -> None:
    """Guards the contract that floating branch coexists with old generation refs."""
    _working, remote, initial = _remote(tmp_path)
    root = tmp_path / "sources"
    repository = root
    pool = _ensure_pool(repository, str(remote))
    legacy_branch = f"godoo/legacy/19.0/{initial}"
    _git(pool, "fetch", str(remote), f"19.0:refs/heads/{legacy_branch}")
    legacy = repository / "old-worktree"
    _git(pool, "worktree", "add", "--relative-paths", str(legacy), legacy_branch)
    (legacy / "shared.txt").write_text("old consumer edit\n")

    selected, commit = _selection(remote, root)

    assert commit == initial
    assert Path(selected) != legacy
    assert (legacy / "shared.txt").read_text() == "old consumer edit\n"
    assert _git(legacy, "symbolic-ref", "--short", "HEAD") == legacy_branch


def test_outer_workspace_lock_initializes_a_fresh_pool(tmp_path: Path) -> None:
    """Guards the contract that outer workspace lock initializes a fresh pool."""
    _working, remote, initial = _remote(tmp_path)
    root = tmp_path / "sources"

    with repository_lock(root, write=True):
        source = sync_repo_unlocked(
            GodooGitRepo(url=str(remote), branch="19.0"),
            root=root,
            default_branch="19.0",
            role="odoo",
        )

    assert (root / ".godoo.lock").is_file()
    assert (root / ".git").is_dir()
    assert source.host_path == root / "19.0"
    assert source.resolved_commit == initial


def test_sync_refuses_an_existing_unmarked_pool(tmp_path: Path) -> None:
    """Do not adopt a bare pool merely because its directory matches a manifest URL."""
    working, remote, _initial = _remote(tmp_path)
    root = tmp_path / "sources"
    pool = repository_root(root, str(remote)) / ".git"
    pool.parent.mkdir(parents=True)
    _git(working, "clone", "--bare", str(remote), str(pool))

    with pytest.raises(WorkspaceError, match="Refusing to mark an existing repository as managed"):
        _sync_repo(GodooGitRepo(url=str(remote), branch="19.0"), root=root, default_branch="19.0", role="odoo")

    assert not (pool.parent / ".godoo.lock").exists()


def test_manifest_addon_aliases_share_objects_and_working_files(tmp_path: Path) -> None:
    """Guards the contract that manifest addon aliases share objects and working files."""
    _working, remote, _initial = _remote(tmp_path)
    spec = GodooGitRepo(url=str(remote), branch="19.0")
    first = _sync_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="addon", prefix="First")
    second = _sync_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="addon", prefix="Second")

    assert first.host_path != second.host_path
    assert first.container_path != second.container_path


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://github.com/OCA/server-tools.git", "https://github.com/OCA/server-tools/"),
        ("git@github.com:OCA/server-tools.git", "https://github.com/OCA/server-tools"),
        ("ssh://git@github.com/OCA/server-tools.git", "git@github.com:OCA/server-tools"),
        ("https://gitlab.example.test/team/addons.git", "https://gitlab.example.test/team/addons"),
        (
            "[git@gitlab.wetech.local:222]:wetech/odoo-enterprise.git",
            "ssh://git@gitlab.wetech.local:222/wetech/odoo-enterprise.git",
        ),
    ],
)
def _assert_repository_url_aliases_reuse_pool(tmp_path: Path, left: str, right: str) -> None:
    """Guards the contract that repository url aliases reuse pool."""
    root = repository_root(tmp_path, left)
    assert root == repository_root(tmp_path, right)
    assert _ensure_pool(root, left) == _ensure_pool(root, right)


def test_file_url_decodes_spaces_and_distinct_repositories_stay_distinct(tmp_path: Path) -> None:
    """Guards the contract that file url decodes spaces and distinct repositories stay distinct."""
    path = tmp_path / "with spaces.git"
    assert GitUrl(str(path)).canonical == GitUrl(path.as_uri()).canonical
    assert GitUrl(str(path)).canonical != GitUrl(str(tmp_path / "another repository.git")).canonical
    _assert_repository_url_aliases_reuse_pool(
        tmp_path,
        "https://github.com/OCA/server-tools.git",
        "https://github.com/OCA/server-tools/",
    )
    assert GitUrl("https://host.test/one/repo").canonical != GitUrl("https://host.test/two/repo").canonical
    assert GitUrl("https://one.test/team/repo").canonical != GitUrl("https://two.test/team/repo").canonical
    assert GitUrl("git@host.test:relative/repo").canonical != GitUrl("ssh://git@host.test/relative/repo").canonical
    assert GitUrl("ssh://first@host.test/repo").canonical != GitUrl("ssh://second@host.test/repo").canonical


@pytest.mark.parametrize(
    "url",
    ["https://token@github.com/team/repo", "https://user:secret@host.test/repo", "https://host.test/repo?token=secret"],
)
def test_url_credentials_are_rejected_without_echoing_secrets(url: str) -> None:
    """Guards the contract that url credentials are rejected without echoing secrets."""
    with pytest.raises(ValueError, match="credential") as error:
        GitUrl(url)
    assert url not in str(error.value)
    assert "secret" not in str(error.value)


def test_shallow_related_history_merges_and_is_reproducible_across_roots(tmp_path: Path) -> None:
    """Guards the contract that shallow related history merges and is reproducible across roots."""
    working, remote, initial = _remote(tmp_path)
    _git(working, "checkout", "-b", "feature")
    feature = _commit(working, "feature.txt", "feature\n")
    _git(working, "checkout", "19.0")
    base = _commit(working, "base.txt", "base\n")
    _git(working, "push", str(remote), "19.0", "feature")
    spec = _merge_spec(remote)

    first = _sync_repo(spec, root=tmp_path / "first", default_branch="19.0", role="odoo")
    second = _sync_repo(spec, root=tmp_path / "second", default_branch="19.0", role="odoo")

    assert first.resolved_commit == second.resolved_commit
    assert _git(first.host_path, "show", "-s", "--format=%P") == f"{base} {feature}"
    assert _git(first.host_path, "merge-base", base, feature) == initial
    assert (first.host_path / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    assert first.worktree_branch.startswith("godoo/mf-")
    assert _git(first.host_path, "symbolic-ref", "--short", "HEAD") == first.worktree_branch
    assert not any(first.host_path.parent.glob(".godoo-worktree-*"))
    inspected = _inspect_repo(spec, root=tmp_path / "first", default_branch="19.0", role="odoo")
    assert inspected.resolved_commit == first.resolved_commit
    assert inspected.base_commit == first.base_commit
    assert inspected.merge_commits == first.merge_commits


def test_inspect_accepts_a_metadata_only_amended_managed_merge(tmp_path: Path) -> None:
    """Allow an amended merge when its ordered parents, subject, and tree are retained."""
    working, remote, _initial = _remote(tmp_path)
    _git(working, "checkout", "-b", "feature")
    _commit(working, "feature.txt", "feature\n")
    _git(working, "checkout", "19.0")
    _commit(working, "base.txt", "base\n")
    _git(working, "push", str(remote), "19.0", "feature")
    spec = _merge_spec(remote)
    selected = _sync_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="odoo")
    _git(selected.host_path, "config", "user.name", "Maintainer")
    _git(selected.host_path, "config", "user.email", "maintainer@example.test")
    original_subject = _git(selected.host_path, "show", "-s", "--format=%s")
    _git(selected.host_path, "commit", "--amend", "--no-edit")

    inspected = _inspect_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="odoo")

    assert _git(inspected.host_path, "show", "-s", "--format=%s") == original_subject


def test_inspect_rejects_an_amended_merge_with_a_changed_tree(tmp_path: Path) -> None:
    """Do not accept a replacement commit that keeps the generated merge subject."""
    working, remote, _initial = _remote(tmp_path)
    _git(working, "checkout", "-b", "feature")
    _commit(working, "feature.txt", "feature\n")
    _git(working, "checkout", "19.0")
    _commit(working, "base.txt", "base\n")
    _git(working, "push", str(remote), "19.0", "feature")
    spec = _merge_spec(remote)
    selected = _sync_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="odoo")
    _git(selected.host_path, "config", "user.name", "Maintainer")
    _git(selected.host_path, "config", "user.email", "maintainer@example.test")
    original_subject = _git(selected.host_path, "show", "-s", "--format=%s")
    original_parents = _git(selected.host_path, "show", "-s", "--format=%P")
    (selected.host_path / "tampered.txt").write_text("not from the merge recipe\n", encoding="utf-8")
    _git(selected.host_path, "add", "tampered.txt")
    _git(selected.host_path, "commit", "--amend", "--no-edit")

    assert _git(selected.host_path, "show", "-s", "--format=%s") == original_subject
    assert _git(selected.host_path, "show", "-s", "--format=%P") == original_parents
    with pytest.raises(WorkspaceError, match="differs from its configured recipe"):
        _inspect_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="odoo")


def test_checkout_bytes_do_not_depend_on_host_line_ending_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guards the contract that checkout bytes do not depend on host line ending configuration."""
    _working, remote, _initial = _remote(tmp_path)
    config = tmp_path / "gitconfig"
    config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))

    selected, _selected_commit = _selection(remote, tmp_path / "sources")

    assert (Path(selected) / "shared.txt").read_bytes() == b"original\n"


def test_merge_order_is_part_of_recipe_identity(tmp_path: Path) -> None:
    """Guards the contract that merge order is part of recipe identity."""
    working, remote, initial = _remote(tmp_path)
    _git(working, "checkout", "-b", "feature")
    _commit(working, "first.txt", "first\n")
    _git(working, "checkout", "-b", "other", initial)
    _commit(working, "second.txt", "second\n")
    _git(working, "push", str(remote), "feature", "other")
    spec = _merge_spec(remote)
    spec.merge_from.append(GitMergeSource(url=remote.as_uri(), branch="other"))
    first = _sync_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="odoo")
    spec.merge_from.reverse()
    second = _sync_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="odoo")

    assert first.host_path != second.host_path
    assert first.resolved_commit != second.resolved_commit
    assert _git(first.host_path, "rev-parse", "HEAD^{tree}") == _git(second.host_path, "rev-parse", "HEAD^{tree}")
    assert _inspect_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="odoo").resolved_commit == (
        second.resolved_commit
    )
    _assert_repeated_merge_inputs_reuse_the_same_native_branch(tmp_path / "repeated-merge")


def test_failed_merge_removes_staging_worktree_and_preserves_published_sources(tmp_path: Path) -> None:
    """Guards the contract that failed merge removes staging worktree and preserves published sources."""
    working, remote, _initial = _remote(tmp_path)
    root = tmp_path / "sources"
    first, first_commit = _selection(remote, root)
    _git(working, "checkout", "-b", "feature")
    _commit(working, "shared.txt", "feature\n")
    _git(working, "checkout", "19.0")
    _commit(working, "shared.txt", "base\n")
    _git(working, "push", str(remote), "19.0", "feature")

    with pytest.raises(WorkspaceError, match="CONFLICT"):
        _sync_repo(_merge_spec(remote), root=root, default_branch="19.0", role="odoo")

    assert _git(Path(first), "rev-parse", "HEAD") == first_commit
    assert (Path(first) / "shared.txt").read_text(encoding="utf-8") == "original\n"
    pool = _worktree_pool(Path(first))
    assert _git(pool, "worktree", "list", "--porcelain").count("worktree ") == 2
    assert not any(Path(first).parent.glob(".godoo-worktree-*"))
    assert not _git(pool, "for-each-ref", "refs/heads/godoo/merge")


def test_unrelated_histories_fail_without_publishing_worktree(tmp_path: Path) -> None:
    """Guards the contract that unrelated histories fail without publishing worktree."""
    _working, remote, _initial = _remote(tmp_path / "base")
    _other_working, other_remote, _other = _remote(tmp_path / "other")
    spec = GodooGitRepo(
        url=remote.as_uri(), branch="19.0", merge_from=[GitMergeSource(url=other_remote.as_uri(), branch="19.0")]
    )
    # Different content ensures the two repositories do not share their initial commit.
    _commit(_other_working, "shared.txt", "different history\n")
    _git(_other_working, "checkout", "--orphan", "unrelated")
    _git(_other_working, "commit", "-m", "unrelated root")
    _git(_other_working, "push", str(other_remote), "unrelated:19.0", "--force")

    with pytest.raises(WorkspaceError, match="no common ancestor"):
        _sync_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="odoo")
    assert not list((tmp_path / "sources").glob("*/worktrees/*"))


def test_concurrent_syncs_publish_exactly_one_worktree(tmp_path: Path) -> None:
    """Guards the contract that concurrent syncs publish exactly one worktree."""
    _working, remote, _initial = _remote(tmp_path)
    root = tmp_path / "sources"
    _ensure_pool(root, str(remote))
    with ProcessPoolExecutor(max_workers=4) as executor:
        pending = [executor.submit(_selection, remote, root) for _ in range(4)]
        results = []
        for task in pending:
            results.append(task.result())

    assert results
    assert len(set(results)) == 1
    assert _selection(remote, root) == results[0]
    path = Path(results[0][0])
    assert _git(_worktree_pool(path), "worktree", "list", "--porcelain").count("worktree ") == 2
    assert not any(path.parent.glob(".godoo-worktree-*"))


def _assert_existing_floating_worktree_is_reset(tmp_path: Path) -> None:
    """Guards the contract that existing floating worktree is reset."""
    working, remote, initial = _remote(tmp_path)
    advanced = _commit(working, "shared.txt", "advanced\n")
    _git(working, "push", str(remote), "19.0")
    root = tmp_path / "sources"
    _sync_repo(
        GodooGitRepo(url=str(remote), branch="19.0", commit=initial), root=root, default_branch="19.0", role="odoo"
    )
    selected, _selected_commit = _selection(remote, root)
    _git(Path(selected), "checkout", "--detach", initial)

    selected_again, selected_commit = _selection(remote, root)

    assert advanced != initial
    assert selected_again == selected
    assert selected_commit == advanced
    assert _git(Path(selected), "rev-parse", "HEAD") == advanced


def test_ignored_changes_are_removed_on_sync(tmp_path: Path) -> None:
    """Guards the contract that ignored changes are removed on sync."""
    working, remote, _initial = _remote(tmp_path)
    _commit(working, ".gitignore", "ignored.txt\n")
    _git(working, "push", str(remote), "19.0")
    root = tmp_path / "sources"
    selected, _selected_commit = _selection(remote, root)
    (Path(selected) / "ignored.txt").write_text("unsupported", encoding="utf-8")

    _selection(remote, root)
    assert not (Path(selected) / "ignored.txt").exists()


def test_ignored_finder_metadata_does_not_make_worktree_dirty(tmp_path: Path) -> None:
    """Guards the narrow exception for ignored Finder metadata."""
    working, remote, _initial = _remote(tmp_path)
    _commit(working, ".gitignore", ".DS_Store\nignored.txt\n")
    _git(working, "push", str(remote), "19.0")
    root = tmp_path / "sources"
    selected, _selected_commit = _selection(remote, root)
    path = Path(selected)
    (path / ".DS_Store").write_bytes(b"finder metadata")
    (path / "ignored.txt").write_text("unsupported", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="Managed source is dirty"):
        _assert_clean(path)
    (path / "ignored.txt").unlink()
    _assert_clean(path)
    (path / ".DS_Store").unlink()
    (path / ".DS_Store").mkdir()
    (path / ".DS_Store" / "nested").write_bytes(b"finder metadata directory")
    with pytest.raises(WorkspaceError, match="Managed source is dirty"):
        _assert_clean(path)


def test_unrelated_named_worktree_at_expected_path_is_preserved(tmp_path: Path) -> None:
    """Guards the contract that unrelated named worktree at expected path is preserved."""
    _working, remote, _initial = _remote(tmp_path)
    root = tmp_path / "sources"
    selected, _commit_id = _selection(remote, root)
    path = Path(selected)
    _git(path, "switch", "-C", "consumer/other")
    (path / "keep.txt").write_text("keep\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="occupied by another branch"):
        _selection(remote, root)
    assert _git(path, "symbolic-ref", "--short", "HEAD") == "consumer/other"
    assert (path / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_inspect_rejects_extra_floating_commit_until_sync_repairs_it(tmp_path: Path) -> None:
    """Guards the contract that inspect rejects extra floating commit until sync repairs it."""
    _working, remote, initial = _remote(tmp_path)
    root = tmp_path / "sources"
    selected, _selected_commit = _selection(remote, root)
    _git(Path(selected), "config", "user.name", "Manual")
    _git(Path(selected), "config", "user.email", "manual@example.test")
    extra = _commit(Path(selected), "local.txt", "local\n")

    with pytest.raises(WorkspaceError, match="differs from its configured ref"):
        _inspect_repo(GodooGitRepo(url=str(remote), branch="19.0"), root=root, default_branch="19.0", role="odoo")

    repaired = _sync_repo(GodooGitRepo(url=str(remote), branch="19.0"), root=root, default_branch="19.0", role="odoo")
    assert repaired.resolved_commit == initial
    assert repaired.resolved_commit != extra
    assert not (Path(selected) / "local.txt").exists()


def test_sync_rebuilds_from_current_inputs_after_branches_advance(tmp_path: Path) -> None:
    """Guards the contract that sync rebuilds from current inputs after branches advance."""
    working, remote, _initial = _remote(tmp_path)
    _git(working, "checkout", "-b", "feature")
    _commit(working, "feature.txt", "feature\n")
    _git(working, "checkout", "19.0")
    _commit(working, "base.txt", "base\n")
    _git(working, "push", str(remote), "19.0", "feature")
    spec = _merge_spec(remote)
    original = _sync_repo(spec, root=tmp_path / "first", default_branch="19.0", role="odoo")
    _commit(working, "next.txt", "advanced\n")
    _git(working, "push", str(remote), "19.0")

    replay = _sync_repo(spec, root=tmp_path / "second", default_branch="19.0", role="odoo")

    assert replay.resolved_commit != original.resolved_commit
    assert replay.base_commit != original.base_commit
    assert replay.merge_commits == original.merge_commits
    assert replay.branch == "19.0"
    assert replay.requested_commit == ""
    assert (replay.host_path / "next.txt").exists()


def test_worktree_metadata_remains_relative_when_source_root_moves(tmp_path: Path) -> None:
    """Guards the contract that worktree metadata remains relative when source root moves."""
    _working, remote, initial = _remote(tmp_path)
    original = tmp_path / "sources"
    selected, _selected_commit = _selection(remote, original)
    moved = tmp_path / "moved"
    original.rename(moved)
    path = moved / Path(selected).relative_to(original)

    assert _git(path, "rev-parse", "HEAD") == initial
    assert str(original) not in (path / ".git").read_text(encoding="utf-8")
    assert _worktree_pool(path) == path.parent / ".git"


def test_native_worktree_move_is_reconciled_to_its_readable_path(tmp_path: Path) -> None:
    """Guards the contract that native worktree move is reconciled to its readable path."""
    _working, remote, initial = _remote(tmp_path)
    root = tmp_path / "sources"
    selected, _selected_commit = _selection(remote, root)
    original = Path(selected)
    moved = root / "my-odoo-checkout"
    pool = _worktree_pool(original)
    _git(pool, "worktree", "move", str(original), str(moved))

    assert _selection(remote, root) == (str(original), initial)
    assert original.exists()
    assert not moved.exists()
    assert _git(pool, "worktree", "list", "--porcelain").count("worktree ") == 2


def _legacy_clean_detached_case(tmp_path: Path) -> None:
    """Guards the contract that sync replaces a clean detached legacy worktree."""
    _working, remote, initial = _remote(tmp_path)
    root = tmp_path / "sources"
    selected, _selected_commit = _selection(remote, root)
    readable = Path(selected)
    pool = _worktree_pool(readable)
    legacy = root / "worktrees" / "godoo%2F19.0"
    legacy.parent.mkdir()
    _git(pool, "worktree", "move", str(readable), str(legacy))
    _git(pool, "worktree", "add", "--detach", str(readable), initial)

    synced, resolved = _selection(remote, root)

    assert (synced, resolved) == (str(readable), initial)
    assert not legacy.exists()
    assert _git(readable, "symbolic-ref", "--short", "HEAD") == "godoo/19.0"
    assert _git(pool, "worktree", "list", "--porcelain").count("worktree ") == 2


def _legacy_dirty_detached_case(tmp_path: Path) -> None:
    """Guards the contract that sync preserves a dirty detached legacy worktree."""
    _working, remote, initial = _remote(tmp_path)
    root = tmp_path / "sources"
    selected, _selected_commit = _selection(remote, root)
    readable = Path(selected)
    pool = _worktree_pool(readable)
    legacy = root / "worktrees" / "godoo%2F19.0"
    legacy.parent.mkdir()
    _git(pool, "worktree", "move", str(readable), str(legacy))
    _git(pool, "worktree", "add", "--detach", str(readable), initial)
    (readable / "shared.txt").write_text("keep this edit\n", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="dirty"):
        _selection(remote, root)

    assert legacy.exists()
    assert (readable / "shared.txt").read_text(encoding="utf-8") == "keep this edit\n"


def _legacy_occupied_path_case(tmp_path: Path) -> None:
    """Guards the contract that sync preserves an arbitrary path at the readable location."""
    _working, remote, _initial = _remote(tmp_path)
    root = tmp_path / "sources"
    selected, _selected_commit = _selection(remote, root)
    readable = Path(selected)
    pool = _worktree_pool(readable)
    legacy = root / "worktrees" / "godoo%2F19.0"
    legacy.parent.mkdir()
    _git(pool, "worktree", "move", str(readable), str(legacy))
    readable.mkdir()

    with pytest.raises(WorkspaceError, match="occupied"):
        _selection(remote, root)

    assert legacy.exists()
    assert readable.is_dir()


def _legacy_removed_native_case(tmp_path: Path) -> None:
    """Guards the contract that removed native worktree is recreated from its stable branch."""
    _working, remote, initial = _remote(tmp_path)
    root = tmp_path / "sources"
    selected, _selected_commit = _selection(remote, root)
    pool = _worktree_pool(Path(selected))
    _git(pool, "worktree", "remove", "--force", selected)

    recreated, recreated_commit = _selection(remote, root)

    assert recreated == selected
    assert recreated_commit == initial
    assert _git(Path(recreated), "symbolic-ref", "--short", "HEAD") == "godoo/19.0"


@pytest.mark.parametrize(
    "case",
    ["clean-detached", "dirty-detached", "occupied-path", "removed-native"],
    ids=["clean-detached", "dirty-detached", "occupied-path", "removed-native"],
)
def test_legacy_worktree_migration_matrix(tmp_path: Path, case: str) -> None:
    """Covers legacy worktree migration and preservation safety cases."""
    cases = {
        "clean-detached": _legacy_clean_detached_case,
        "dirty-detached": _legacy_dirty_detached_case,
        "occupied-path": _legacy_occupied_path_case,
        "removed-native": _legacy_removed_native_case,
    }
    cases[case](tmp_path)


def test_sharedrepository_lock_does_not_create_missing_state(tmp_path: Path) -> None:
    """Guards the contract that shared repository lock does not create missing state."""
    pool_root = repository_root(tmp_path / "sources", "https://example.test/source.git")

    with pytest.raises(WorkspaceError, match="is missing"), repository_lock(pool_root, write=False):
        pass

    assert not pool_root.exists()


@pytest.mark.parametrize("component", [".git", ".godoo.lock", "checkout"])
def test_sync_rejects_symlinked_managed_paths_before_reset(tmp_path: Path, component: str) -> None:
    """Guards the contract that sync rejects symlinked managed paths before reset."""
    _working, remote, _initial = _remote(tmp_path)
    root = tmp_path / "sources"
    selected, _commit_id = _selection(remote, root)
    path = Path(selected)
    (path / "shared.txt").write_text("keep this edit\n")
    component_path = path if component == "checkout" else path.parent / component
    moved = tmp_path / "moved-component"
    component_path.rename(moved)
    component_path.symlink_to(moved, target_is_directory=moved.is_dir())

    with pytest.raises(WorkspaceError, match=r"symlink|outside"):
        _selection(remote, root)

    assert (path / "shared.txt").read_text() == "keep this edit\n"


def test_changed_merge_input_rebuilds_the_stable_branch(tmp_path: Path) -> None:
    """Guards the contract that changed merge input rebuilds the stable branch."""
    working, remote, _initial = _remote(tmp_path)
    _git(working, "checkout", "-b", "feature")
    _commit(working, "feature.txt", "first\n")
    _git(working, "push", str(remote), "feature")
    spec = _merge_spec(remote)
    root = tmp_path / "sources"
    first = _sync_repo(spec, root=root, default_branch="19.0", role="odoo")
    assert _sync_repo(spec, root=root, default_branch="19.0", role="odoo") == first
    _commit(working, "feature.txt", "second\n")
    _git(working, "push", str(remote), "feature")

    second = _sync_repo(spec, root=root, default_branch="19.0", role="odoo")

    assert first.worktree_branch == second.worktree_branch
    assert first.host_path == second.host_path
    assert _git(first.host_path, "rev-parse", "HEAD") == second.resolved_commit
    assert (first.host_path / "feature.txt").read_text(encoding="utf-8") == "second\n"
    assert (second.host_path / "feature.txt").read_text(encoding="utf-8") == "second\n"


def test_extra_commit_on_a_managed_merge_branch_is_discarded_on_sync(tmp_path: Path) -> None:
    """Guards the contract that extra commit on a managed merge branch is discarded on sync."""
    working, remote, _initial = _remote(tmp_path)
    _git(working, "checkout", "-b", "feature")
    _commit(working, "feature.txt", "feature\n")
    _git(working, "push", str(remote), "feature")
    spec = _merge_spec(remote)
    root = tmp_path / "sources"
    first = _sync_repo(spec, root=root, default_branch="19.0", role="odoo")
    _git(first.host_path, "config", "user.name", "Manual")
    _git(first.host_path, "config", "user.email", "manual@example.test")
    extra = _commit(first.host_path, "extra.txt", "manual change\n")

    rebuilt = _sync_repo(spec, root=root, default_branch="19.0", role="odoo")

    assert extra != rebuilt.resolved_commit
    assert _git(first.host_path, "rev-parse", "HEAD") == rebuilt.resolved_commit
    assert not (first.host_path / "extra.txt").exists()


def _assert_repeated_merge_inputs_reuse_the_same_native_branch(tmp_path: Path) -> None:
    """Guards the contract that repeated merge inputs reuse the same native branch."""
    working, remote, _initial = _remote(tmp_path)
    _git(working, "checkout", "-b", "feature")
    _commit(working, "feature.txt", "feature\n")
    _git(working, "push", str(remote), "feature")
    spec = _merge_spec(remote)
    spec.merge_from.append(spec.merge_from[0])
    root = tmp_path / "sources"

    first = _sync_repo(spec, root=root, default_branch="19.0", role="odoo")

    assert _sync_repo(spec, root=root, default_branch="19.0", role="odoo") == first
    assert _inspect_repo(spec, root=root, default_branch="19.0", role="odoo") == first


def test_pinned_merge_recipe_inspection_preserves_the_pinned_base(tmp_path: Path) -> None:
    """Guards the contract that pinned merge recipe inspection preserves the pinned base."""
    working, remote, initial = _remote(tmp_path)
    _git(working, "checkout", "-b", "feature")
    feature = _commit(working, "feature.txt", "feature\n")
    _git(working, "checkout", "19.0")
    _git(working, "push", str(remote), "feature")
    spec = GodooGitRepo(
        url=str(remote),
        branch="19.0",
        commit=initial,
        merge_from=[GitMergeSource(url=str(remote), branch="feature")],
    )
    root = tmp_path / "sources"
    synced = _sync_repo(spec, root=root, default_branch="19.0", role="odoo")
    inspected = _inspect_repo(spec, root=root, default_branch="19.0", role="odoo")

    assert inspected.base_commit == initial
    assert inspected.merge_commits == (feature,)
    assert inspected.resolved_commit == synced.resolved_commit


def test_manifest_workspace_options_roundtrip_and_select_a_stable_branch(tmp_path: Path) -> None:
    """Guards the contract that manifest namespace roundtrips and selects a stable branch."""
    _working, remote, _initial = _remote(tmp_path)
    data = {
        "url": str(remote),
        "branch": "19.0",
        "worktree_branch": "godoo/wetech",
        "shallow": True,
    }
    spec = GodooGitRepo.from_dict(data)
    assert spec.to_dict() == data
    source = _sync_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="odoo")
    assert source.worktree_branch == "godoo/wetech/19.0"
    assert _git(source.host_path, "symbolic-ref", "--short", "HEAD") == source.worktree_branch
    assert not _git(_worktree_pool(source.host_path), "for-each-ref", "refs/godoo")
    assert not (_worktree_pool(source.host_path) / "godoo.lock").exists()


def test_manifest_shallow_option_requires_a_boolean() -> None:
    """Guards against quoted values silently enabling shallow history."""
    with pytest.raises(TypeError, match="must be true or false"):
        GodooGitRepo.from_dict({"url": "https://example.test/odoo.git", "shallow": "true"})


def test_invalid_manifest_branch_namespace_fails_before_fetch(tmp_path: Path) -> None:
    """Guards the contract that invalid manifest branch namespace fails before fetch."""
    spec = GodooGitRepo(url=str(tmp_path / "missing.git"), worktree_branch="godoo/../bad")
    with pytest.raises(WorkspaceError, match="valid branch name"):
        _sync_repo(spec, root=tmp_path / "sources", default_branch="19.0", role="odoo")
    assert not (tmp_path / "sources").exists()


@pytest.mark.skipif(os.geteuid() != 0, reason="Creating a foreign-owned checkout requires root.")
def test_relative_worktree_probe_ignores_current_checkout_ownership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guards the contract that relative worktree probe ignores current checkout ownership."""
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init")
    subprocess.run(["chown", "-R", "1001:1001", str(foreign)], check=True)
    monkeypatch.chdir(foreign)

    ensure_git_supports_relative_worktrees()


def test_invalid_pinned_commit_reports_workspace_error(tmp_path: Path) -> None:
    """Guards the contract that invalid pinned commit reports workspace error."""
    _working, remote, _initial = _remote(tmp_path)

    with pytest.raises(WorkspaceError, match="Pinned commit 'invalid-pin' could not be resolved"):
        _sync_repo(
            GodooGitRepo(url=str(remote), branch="19.0", commit="invalid-pin"),
            root=tmp_path / "sources",
            default_branch="19.0",
            role="odoo",
        )
