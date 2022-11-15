import logging
from pathlib import Path

from git import InvalidGitRepositoryError, Repo

from .zip_download import git_download_zip

LOGGER = logging.getLogger(__name__)


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
        will call reset --hard after checkout to ensure clean git repo, by default True
    """

    try:
        if pull:
            LOGGER.debug("Pulling Repo: %s, %s", repo.git_dir, pull)
            repo.remotes[0].pull(pull)
        if commit:
            if str(repo.head.commit) != str(commit):
                LOGGER.debug("Checking out %s to Commit: %s", repo.git_dir, commit)
                repo.git.checkout(commit)
            return
        if branch:
            LOGGER.debug("Checking Out repo %s to Branch: %s", repo.git_dir, branch)
            repo.git.checkout(branch)
        if reset_hard:
            repo.git.reset("--hard")
    except Exception as e:
        raise Exception(
            str(f"Error while ensuring Git repo: {str(repo)}, branch='{branch}', commit='{commit}', fetch='{pull}'")
        ) from e


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
        current = repo.head.commit

        if not pull and commit and str(commit) != str(repo.head.commit):
            pull = str(commit)

        if not pull and branch and not commit:
            pull = str(branch)
        git_pull_checkout_reset(repo=repo, branch=branch, commit=commit, pull=pull)

        if current == repo.head.commit:
            LOGGER.info("Repo Commit matches. Skipping: '%s' --> '%s'", repo_src, target_folder)
        else:
            LOGGER.info("Pulled Repo from: '%s'. Head is now at: %s", repo_src, repo.head.commit)
    except InvalidGitRepositoryError:
        LOGGER.debug("Cloning Repo: %s, to '%s', branch '%s'", repo_src, target_folder, branch)
        repo = Repo.clone_from(repo_src, target_folder, branch=branch, **kwargs)
        git_pull_checkout_reset(repo=repo, branch=branch, commit=commit, pull=pull)

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

    if target_folder.exists() and any(target_folder.iterdir()) and not target_folder.glob(".git"):
        LOGGER.info("Assuming '%s' got pulled in .Zip mode. Skipping Clone and commit check.", target_folder)
        zip_mode = True

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
