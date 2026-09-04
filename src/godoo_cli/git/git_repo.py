"""Clone repositories and select their requested Git refs."""

import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from git import GitCommandError, InvalidGitRepositoryError, Repo

from .git_url import GitUrl
from .zip_download import git_download_zip

LOGGER = logging.getLogger(__name__)


def _git_clean_clone(repo_src: str, target_folder: Path, **kwargs: Any) -> Repo:
    """Replace a target directory with a fresh clone."""
    LOGGER.debug("Cloning Repo: %s, to '%s', Kwargs: '%s'", repo_src, target_folder, kwargs)
    if not isinstance(target_folder, Path):
        target_folder = Path(target_folder)
    if target_folder.exists():
        LOGGER.debug("Clearing Repo folder: %s", target_folder)
        shutil.rmtree(target_folder)
    repo = Repo.clone_from(repo_src, target_folder, **kwargs)
    return repo


def git_pull_checkout_reset(
    repo: Repo, branch: str = "master", commit: str = "", pull: str = "", reset_hard: bool = True
):
    """Move a repository to the requested ref, discarding tracked changes by default.

    A pull that cannot fast-forward because histories diverged replaces the
    checkout with a fresh clone. Other Git failures are propagated.

    Raises:
        GitCommandError: If the pull fails for a reason that cannot be repaired by recloning.
    """
    if reset_hard:
        repo.git.reset("--hard", "HEAD")

    if pull:
        LOGGER.debug("Pulling Repo: %s, %s", repo.working_dir, pull)
        try:
            repo.remotes[0].pull(None if isinstance(pull, bool) else pull, ff_only=True)
        except GitCommandError as e:
            if (
                "fatal: refusing to merge unrelated histories" in e.stderr
                or "fatal: Not possible to fast-forward" in e.stderr
            ):
                clone_kwargs = {
                    "filter": "blob:none",
                    "single_branch": True,
                }
                if branch:
                    clone_kwargs["branch"] = branch
                LOGGER.warning(
                    "Repo %s needs to be re-cloned due to unrelated histories or non-fast-forwardable changes.",
                    repo.working_dir,
                )
                repo = _git_clean_clone(repo.remotes[0].url, Path(repo.working_dir), **clone_kwargs)
            else:
                raise e
    if commit:
        if str(repo.head.commit) != str(commit):
            LOGGER.debug("Checking out %s to Commit: %s", repo.git_dir, commit)
            repo.git.checkout(commit)
        return
    if branch:
        LOGGER.debug("Checking Out repo %s to Branch: %s", repo.git_dir, branch)
        repo.git.checkout(branch)


def git_ensure_ref(
    target_folder: Path,
    repo_src: str,
    branch: str = "master",
    commit: str = "",
    pull: str = "",
    **kwargs: Any,
) -> Repo:
    """Clone a repository and select the requested ref."""
    LOGGER.info("Ensuring Repo '%s' Branch: '%s' Commit: '%s' --> '%s'", repo_src, branch, commit, target_folder)
    target_folder.mkdir(exist_ok=True, parents=True)
    try:
        repo = Repo(target_folder)
        current = str(repo.head.commit)

        if not pull and commit and str(commit) != str(current):
            pull = str(commit)

        if not pull and branch and not commit:
            remote_name = str(repo.remotes[0].name)
            try:
                remote_commit = repo.git.rev_parse(remote_name + "/" + str(branch))
            except Exception:
                remote_commit = "unknown"
            LOGGER.debug(
                "Repo: %s comparing local head '%s' with remote head '%s'", repo.working_dir, current, remote_commit
            )
            if str(remote_commit) != current:
                pull = str(branch)

        git_pull_checkout_reset(repo=repo, branch=branch, commit=commit, pull=pull)
        repo = Repo(target_folder)  # re-instantiate to update head info after pull/checkout

        if current == str(repo.head.commit):
            LOGGER.debug("Repo Commit matches. Skipping: '%s' --> '%s'", repo_src, current)
        else:
            LOGGER.info("Pulled Repo from: '%s'. Head is now at: %s", repo_src, repo.head.commit)
    except InvalidGitRepositoryError:
        repo = _git_clean_clone(repo_src, target_folder, branch=branch, **kwargs)
        git_pull_checkout_reset(repo=repo, branch=branch, commit=commit)

        LOGGER.info(
            "Cloned Repo: '%s'. Branch='%s' Head='%s'",
            repo_src,
            repo.active_branch.name if not repo.head.is_detached else "Detached",
            repo.head.commit,
        )
    return repo


def git_ensure_repo(
    target_folder: Path,
    repo_src: str,
    branch: str = "master",
    commit: str = "",
    pull: str = "",
    zip_mode: bool = False,
    **kwargs: Any,
) -> Optional[Repo]:
    """Materialize a repository as a clone or archive."""
    if isinstance(target_folder, str):
        target_folder = Path(target_folder)

    if target_folder.exists() and any(target_folder.iterdir()) and not target_folder.glob(".git"):
        LOGGER.info("Assuming '%s' got pulled in .Zip mode. Skipping Clone and commit check.", target_folder)
        zip_mode = True

    if zip_mode and GitUrl(repo_src).url_type == "ssh":
        LOGGER.info("Zip downloading currently not supported for SSH type Urls")
        zip_mode = False

    if zip_mode:
        return git_download_zip(
            target_folder=target_folder,
            repo_url=repo_src,
            branch=branch,
            commit=commit,
        )
    return git_ensure_ref(
        target_folder=target_folder,
        repo_src=repo_src,
        branch=branch,
        commit=commit,
        pull=pull,
        **kwargs,
    )
