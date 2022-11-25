import logging
import tempfile
import zipfile
from pathlib import Path

from ..helpers.system import download_file
from .git_url import GitUrl

LOGGER = logging.getLogger(__name__)


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
    download_url = git_url.get_archive_url(ref=commit or branch)
    with tempfile.TemporaryDirectory() as tdir:
        zip_path = Path(tdir) / f"{git_url.name}.zip"
        LOGGER.info("Downloading GitRepo Zip: '%s'", download_url)
        LOGGER.debug("Target Path: '%s' ", zip_path)
        download_file(download_url, zip_path)
        if not zip_path.exists():
            raise FileNotFoundError(f"Could not download Repo Zip from: {download_url}")
        LOGGER.info("Extracting GitRepo Zip: %s", git_url.name)
        ex_location = Path(tdir) / "extract"
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(ex_location)
        for path in ex_location.glob("*"):
            LOGGER.info("Moving %s to %s", path.stem, target_folder)
            path.rename(target_folder)
            break
