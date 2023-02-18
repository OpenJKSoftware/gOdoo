""" Module to provide GIT interaction."""
import logging
import shutil
from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, Repo

from .git_url import GitUrl
from .zip_download import git_download_zip

LOGGER = logging.getLogger(__name__)


def _git_clean_clone(repo_src: str, target_folder: Path, **kwargs):
    """Clears targetfolder and does a clean clone_from

    Parameters
    ----------
    repo_src : str
        Clone url
    target_folder : Path
        Download folder
    branch : str
        branch to clone

    Returns
    -------
    _type_re
        _description_
    """
    LOGGER.debug("Cloning Repo: %s, to '%s', Kwargs: '%s'", repo_src, target_folder, kwargs)
    if not isinstance(target_folder, Path):
        target_folder = Path(target_folder)
    if target_folder.exists():
        LOGGER.debug("Clearing Repo folder: %s", target_folder)
        shutil.rmtree(target_folder)
    return Repo.clone_from(repo_src, target_folder, **kwargs)


def git_pull_checkout_reset(
    repo: Repo, branch: str = "master", commit: str = "", pull: str = "", reset_hard: bool = True
):
    """
    Ensure git Repo is on specific branch and commit.
    Will unshallow if neccessary

    Parameters
    ----------
    repo : Repo
        Git Repo
    branch : str, optional
        branch to checkout, by default "master"
    commit : str, optional
        specific commitSHA to checkout, by default ""
    pull : str, optional
        specific target to pull. Usually used in conjunction with branch.
    reset_hard : bool, optional
        will call reset --hard before checkout to ensure clean git repo, by default True
    """

    if reset_hard:
        repo.git.reset("--hard", "HEAD")

    if pull:
        LOGGER.debug("Pulling Repo: %s, %s", repo.working_dir, pull)
        try:
            repo.remotes[0].pull(pull)
        except GitCommandError as e:
            if "fatal: refusing to merge unrelated histories" in e.stderr:
                clone_kwargs = {}
                if branch:
                    clone_kwargs["branch"] = branch
                repo = _git_clean_clone(repo.remotes[0].url, repo.working_dir, **clone_kwargs)
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
    **kwargs,
):
    """
    Clone a git Repo and ensure its HEAD is set to Branch and commit.


    Parameters
    ----------
    target_folder : Path
        "target folder name"
    repo_src : str
        "repo source url"
    commit : str, optional
        commit on which to set the head
    pull : str, optional
        specific target to pull. Usually used in conjunction with branch.
    branch : str, optional
        branch on which to set head, by default 'master'
    **kwargs
        get passed to git clone
    """
    LOGGER.info("Ensuring Repo '%s' --> '%s'", repo_src, target_folder)
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

        if current == str(repo.head.commit):
            LOGGER.info("Repo Commit matches. Skipping: '%s' --> '%s'", repo_src, target_folder)
        else:
            LOGGER.info("Pulled Repo from: '%s'. Head is now at: %s", repo_src, repo.head.commit)
    except InvalidGitRepositoryError:
        repo = _git_clean_clone(repo_src, target_folder, branch=branch, **kwargs)
        git_pull_checkout_reset(repo=repo, branch=branch, commit=commit, pull=False)

        LOGGER.info(
            "Cloned Repo: '%s'. Branch='%s' Head='%s'",
            repo_src,
            repo.active_branch.name if not repo.head.is_detached else "Detached",
            repo.head.commit,
        )
    return repo_src, repo.head.commit


def git_ensure_repo(
    target_folder: Path,
    repo_src: str,
    branch: str = "master",
    commit: str = "",
    pull: str = "",
    zip_mode: bool = False,
    **kwargs,
):
    """Ensures git remote contents are in folder.

    Parameters
    ----------
    target_folder : Path
        Folder to clone/download into
    repo_src : str
        Git Remote Url
    branch : str, optional
        target branch, by default "master"
    commit : str, optional
        target ref, by default ""
    pull : str, optional
        ref to pull, by default ""
    zip_mode : bool, optional
        wether to download a zip (fast but  no commit history) or clone and fetch (fully working git clone), by default False
    **kwargs
        get passed to git clone
    """

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
