"""Git repository archive download module.

This module provides functionality for downloading Git repositories as ZIP archives,
offering a faster alternative to full Git cloning when history is not needed.
It supports various Git hosting services and handles extraction and cleanup.
"""

import logging
import shutil
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

    Raises:
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
            msg = f"Could not download Repo Zip from: {download_url}"
            LOGGER.error(msg)
            raise FileNotFoundError(msg)
        LOGGER.info("Extracting GitRepo Zip: %s", git_url.name)
        ex_location = Path(tdir) / "extract"
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(ex_location)
        for path in ex_location.glob("*"):
            LOGGER.info("Moving %s to %s", path.stem, target_folder)
            shutil.rmtree(target_folder, ignore_errors=True)
            path.rename(target_folder)
            break
