import concurrent.futures
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Literal

from git import Commit, InvalidGitRepositoryError, Repo
from ruamel.yaml import YAML

from .helper import download_file

LOGGER = logging.getLogger(__name__)


class GitUrl:
    """Class to Structurize Git URLs.
    Works with SSH and HTTP(s) URLs
    Can generate Compare Urls
    """

    url: str
    url_type: Literal["http", "ssh"]
    domain: str
    path: str
    user: str
    port: int
    name: str

    def __init__(self, url: str) -> None:
        self.url = url
        if "http" in url:
            http_regex = r"(?P<schema>https?):\/\/(?P<domain>[^\/]+)(?P<path>.*)"
            http_match = re.search(http_regex, url)
            self.url_type = http_match.group("schema")
            self.domain = http_match.group("domain")
            self.path = http_match.group("path")
        else:
            ssh_regex = r"(?P<user>\w+)@(?P<domain>[^:]+):(?:(?P<port>\d+)]?:)?(?P<path>.*)"
            ssh_match = re.search(ssh_regex, url)
            self.url_type = "ssh"
            self.domain = ssh_match.group("domain")
            self.path = ssh_match.group("path")
            self.user = ssh_match.group("user")
            self.port = ssh_match.group("port")

        if self.path.endswith(".git"):  # Todo Python 3.9 Use Removesuffix and RemovePrefix
            self.path = self.path[:-4]
        if self.path.endswith("/"):
            self.path = self.path[:-1]
        if self.path.startswith("/"):
            self.path = self.path[1:]
        self.name = self.path.split("/")[-1]

    def _clean_http_url(self) -> str:
        """Return HTTPs URL without .git suffix.

        Returns
        -------
        str
            Https:// url
        """
        return f"https://{self.domain}/{self.path}"

    def _git_type(self) -> Literal["github", "gitlab"]:
        """Get git Remote type.

        Returns
        -------
        Literal
            "github", "gitlab"

        Raises
        ------
        ValueError
            If Type cannot be determined.
        """
        if "gitlab" in self.domain:
            return "gitlab"
        elif "github" in self.domain:
            return "github"
        else:
            raise ValueError(f"Cant get Git Service type from {self.domain}")

    def get_compare_url(self, from_compare: str, to_compare: str) -> str:
        """Get Compare url between two Refs.

        Parameters
        ----------
        from_compare : str
            from compare ref
        to_compare : str
            to compare ref

        Returns
        -------
        str
            Compare Url Like: https://github.com/odoo/odoo/compare/<commitSHA>...<branchName>
        """
        remote_type = self._git_type()
        if from_compare == to_compare:
            return  # Nothing to Compare here
        http_url = self._clean_http_url()
        if remote_type in ["github", "gitlab"]:
            return f"{http_url}/compare/{from_compare}...{to_compare}"

    def get_archive_url(self, branch: str = "", commit: str = ""):
        if not branch and not commit:
            raise ValueError("Missing either branch or commit to generate Archive URL.")
        http_url = self._clean_http_url()
        remote_type = self._git_type()
        if remote_type == "github":
            return f"{http_url}/archive/{commit or branch }.zip"
        if remote_type == "gitlab":
            return f"{http_url}/-/archive/{commit or branch }/{self.name}.zip"


def yaml_add_compare_commit(repo_dict, compare_target: str):
    """Add comment with Compare URL to Repo:

    Parameters
    ----------
    repo_dict : _type_
        Yaml Dict of url and commit
    compare_target : str
        git ref to compare to
    """
    git_url = GitUrl(repo_dict["url"])
    try:
        compare_url = git_url.get_compare_url(repo_dict["commit"], compare_target)
        repo_dict.yaml_add_eol_comment(compare_url, "commit")
    except Exception as e:
        LOGGER.warn(f"Cannot Generate compare URL for: {git_url.url}")
        LOGGER.debug(e)


def yaml_remove_compare_commit(repo_dict):
    """Remove Comments that have /compare/ in them.

    Parameters
    ----------
    repo_dict : RuamelYaml Dict
        yaml dict
    """
    del_list = []
    for target, comments in repo_dict.ca.items.items():
        for subcomment in comments:
            if subcomment and "/compare/" in subcomment.value:
                del_list.append(target)

    for target in del_list:
        del repo_dict.ca.items[target]


def yaml_roundtrip_loader() -> YAML:
    """Return Ruamel Roundtrip loader.

    Returns
    -------
    YAML
        Yaml Loader
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def git_clone_thirdparty_repos(
    root_folder: Path,
    git_yml_path: Path,
    generate_yml_compare_comments: bool = False,
):
    """
    Clone repos specified in Yml.

    Parameters
    ----------
    root_folder : Path
        clone target folder
    git_yml_path : Path
        yml describing what to clone
    generate_yml_compare_comments : bool, optional
        wether to add github.com three dot compare to repo urls
    """
    yaml = yaml_roundtrip_loader()
    git_repos = yaml.load(git_yml_path.resolve())
    if _git_clone_addon_repos(root_folder=root_folder, git_repos=git_repos):
        _git_clone_thirdparty_repos_update_yml(git_repos, generate_yml_compare_comments)
        LOGGER.info("Updating Git Thirdparty Repo Commit hashes")
        yaml.dump(git_repos, git_yml_path)

    return git_repos


def _git_clone_thirdparty_repos_update_yml(
    repo_yml: Any,
    generate_yml_compare_comments: bool = False,
):
    """Process yaml after thirdparty clone.

    Parameters
    ----------
    repo_yml : Any
        Ruamel Yaml Dict of prefix and list of repos (url,commit,branch)
    generate_yml_compare_comments : bool, optional
        add github compare links as comment to repo yml, by default False
    """
    thirdparty_repos = repo_yml["thirdparty"]
    odoo_default_branch = repo_yml["odoo"]["branch"]
    for prefix in thirdparty_repos:
        for repo in thirdparty_repos[prefix]:
            if generate_yml_compare_comments:
                yaml_add_compare_commit(repo, odoo_default_branch)
            else:
                yaml_remove_compare_commit(repo)


def _git_clone_addon_repos(
    root_folder: Path,
    git_repos: Dict[str, Dict[str, str]],
) -> Dict[str, Commit]:
    """
    Clones Git repos specified in dict into Root folder.
    Ensures repo names are prefixed and uses 8 threads to clone.

    Parameters
    ----------
    root_folder : Path
        clone target folder
    git_repos : Dict[str, Dict[str, str]]
        dict of {parent_folder_name:{"url":clone_url,"branch":clone_branch,"commit":specific_commit_to_clone}}
        branch and commit are optional
        branch defaults to odoo branch from spec file
    Returns
    -------
    Dict[str:Commit]
        dict of {git_src_url:HeadCommit}
    """
    default_branch = git_repos["odoo"].get("branch")
    LOGGER.info("Cloning Thirdparty Addons source.")
    with concurrent.futures.ThreadPoolExecutor(8) as executor:
        futures = []
        thirdparty_repos = git_repos.get("thirdparty")
        if not thirdparty_repos:
            LOGGER.info("No Thirdparty Key in Repospec. Skipping...")
            return
        for prefix, repos in thirdparty_repos.items():
            for repo in repos:
                repo_url = GitUrl(repo["url"])
                name = f"{prefix}_{repo_url.name}"
                futures.append(
                    executor.submit(
                        git_ensure_cloned,
                        Path(root_folder / name),
                        repo_url.url,
                        filter="blob:none",
                        single_branch=True,
                        branch=repo.get("branch", default_branch),
                        commit=repo.get("commit"),
                    )
                )
        clone_results = [f.result() for f in futures]
        clone_results = {r[0]: r[1] for r in clone_results if r}
    return clone_results


def git_ensure_branch_commit(
    repo: Repo, branch: str = "master", commit: str = "", pull: str = "", retry_unshallow: bool = True
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
    retry_unshallow : bool, optional
        retry by unshallowing, by default True
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
    except Exception as e:
        raise Exception(
            str(f"Error while ensuring Git repo: {str(repo)}, branch='{branch}', commit='{commit}', fetch='{pull}'")
        ) from e


def git_ensure_cloned(
    target_folder: Path,
    repo_src: str,
    branch: str = "master",
    commit: str = "",
    pull: str = "",
    **kwargs,
):
    """
    Clone a git Repo and ensure its HEAD is set to Branch and commit.
    **kwargs get passed to git clone

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
    """
    target_folder.mkdir(exist_ok=True, parents=True)
    try:
        repo = Repo(target_folder)
        current = repo.head.commit

        if not pull and commit and str(commit) != str(repo.head.commit):
            pull = str(commit)

        if not pull and branch and not commit:
            pull = str(branch)
        git_ensure_branch_commit(repo=repo, branch=branch, commit=commit, pull=pull)

        if current == repo.head.commit:
            LOGGER.info("No Change in Repo: '%s'", repo_src)
        else:
            LOGGER.info("Pulled Repo from: '%s'. Spec is now at: %s", repo_src, repo.head.commit)
    except InvalidGitRepositoryError:
        LOGGER.debug("Cloning Repo: %s, to '%s', branch '%s'", repo_src, target_folder, branch)
        repo = Repo.clone_from(repo_src, target_folder, branch=branch, **kwargs)
        git_ensure_branch_commit(repo=repo, branch=branch, commit=commit, pull=pull)

        LOGGER.info(
            "Cloned Repo: '%s'. Branch='%s' Head='%s'",
            repo_src,
            repo.active_branch.name if not repo.head.is_detached else "Detached",
            repo.head.commit,
        )
    return repo_src, repo.head.commit


def git_download_zip(repo_url: str, target_folder: Path, branch: str, commit: str = ""):
    """Download Repo Zip from Github.

    Parameters
    ----------
    repo_url : str
        Github Repo Url
    target_folder : Path
        Download Target
    branch : str
        BRanch to download
    commit : str, optional
        Specific Commit to download, by default ""

    Raises
    ------
    FileNotFoundError
        If Download failed
    """
    git_url = GitUrl(repo_url)
    download_url = git_url.get_archive_url(branch=branch, commit=commit)
    with tempfile.TemporaryDirectory() as tdir:
        zip_path = Path(tdir) / f"{git_url.name}.zip"
        LOGGER.info("Downloading GitRepo Zip: '%s' ", download_url)
        LOGGER.debug("Target Path: '%s' ", zip_path)
        download_file(download_url, zip_path)
        if not zip_path.exists():
            raise FileNotFoundError(f"Could not download Repo Zip from: {download_url}")
        LOGGER.info("Extracting GitRepo Zip: %s", git_url.name)
        ex_location = Path(tdir) / "extract"
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(ex_location)
        for path in ex_location.glob("*"):
            LOGGER.debug("Moving %s to %s", path, target_folder)
            path.rename(target_folder)
            break
