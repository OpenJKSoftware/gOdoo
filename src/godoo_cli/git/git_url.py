"""Git URL handling and manipulation module.

This module provides functionality for parsing and manipulating Git URLs,
supporting various Git hosting services and URL formats. It handles
operations like generating raw file URLs and archive download links.
"""

import re
from enum import Enum
from logging import getLogger
from typing import Literal, Optional

LOGGER = getLogger(__name__)


class GitRemoteType(Enum):
    """Enumeration of supported Git remote hosting services.

    This enum defines the Git hosting services that are supported for
    URL parsing and manipulation operations.
    """

    gitlab = "gitlab"
    github = "github"


class GitUrl:
    """Class to Structurize Git URLs.

    Works with SSH and HTTP(s) URLs
    Can generate Compare Urls

    Attributes:
        url: The original Git repository URL.
        url_type: The URL scheme (http, https, or ssh).
        domain: The domain name of the Git service.
        path: The repository path.
        user: The username for SSH URLs.
        port: The port number for SSH URLs.
        name: The repository name.
    """

    url: str
    url_type: Literal["http", "https", "ssh"]
    domain: str
    path: str
    user: str
    port: Optional[int]
    name: str

    def __init__(self, url: str) -> None:
        """Initialize a GitUrl instance with the provided Git repository URL.

        Args:
            url: A Git repository URL (http, https, or ssh format).

        Raises:
            ValueError: If the URL format is invalid or unsupported.
        """
        self.url = url
        if "http" in url:
            http_regex = r"(?P<schema>https?):\/\/(?P<domain>[^\/]+)(?P<path>.*)"
            http_match: Optional[re.Match[str]] = re.search(http_regex, url)
            if not http_match:
                msg = f"Invalid HTTP URL format: {url}"
                LOGGER.error(msg)
                raise ValueError(msg)

            schema = http_match.group("schema")
            if schema not in ("http", "https"):
                msg = f"Invalid schema: {schema}"
                LOGGER.error(msg)
                raise ValueError(msg)
            self.url_type = schema  # Now we can use the actual schema
            self.domain = http_match.group("domain")
            self.path = http_match.group("path")
            self.user = ""  # Not applicable for HTTP
            self.port = None  # Not applicable for HTTP
        else:
            ssh_regex = r"(?P<user>\w+)@(?P<domain>[^:]+):(?:(?P<port>\d+)]?:)?(?P<path>.*)"
            ssh_match: Optional[re.Match[str]] = re.search(ssh_regex, url)
            if not ssh_match:
                msg = f"Invalid SSH URL format: {url}"
                LOGGER.error(msg)
                raise ValueError(msg)

            self.url_type = "ssh"
            self.domain = ssh_match.group("domain")
            self.path = ssh_match.group("path")
            self.user = ssh_match.group("user")
            port_str = ssh_match.group("port")
            self.port = int(port_str) if port_str else None

        self.path = self.path.removesuffix("/")
        self.path = self.path.removesuffix(".git")
        self.path = self.path.removeprefix("/")
        self.name = self.path.split("/")[-1]

    def _clean_http_url(self) -> str:
        """Return HTTPs URL without .git suffix.

        Returns:
        -------
        str
            Https:// url
        """
        return f"https://{self.domain}/{self.path}"

    def _git_type(self) -> GitRemoteType:
        """Get git Remote type.

        Returns:
        -------
        Literal
            "github", "gitlab"

        Raises:
        ------
        ValueError
            If Type cannot be determined.
        """
        if "gitlab" in self.domain:
            return GitRemoteType.gitlab
        if "github" in self.domain:
            return GitRemoteType.github
        msg = f"Cant get Git Service type from {self.domain}"
        LOGGER.error(msg)
        raise ValueError(msg)

    def get_compare_url(self, from_compare: str, to_compare: str) -> str:
        """Get Compare url between two Refs.

        Parameters
        ----------
        from_compare : str
            from compare ref
        to_compare : str
            to compare ref

        Returns:
        -------
        str
            Compare Url Like: https://github.com/odoo/odoo/compare/<commitSHA>...<branchName>
        """
        remote_type = self._git_type()
        if from_compare == to_compare:
            return ""  # Nothing to Compare here
        http_url = self._clean_http_url()
        if remote_type in [GitRemoteType.github, GitRemoteType.gitlab]:
            return f"{http_url}/compare/{from_compare}...{to_compare}"
        return ""

    def get_archive_url(self, ref: str) -> str:
        """Get Download Url for Zip file.

        Parameters
        ----------
        ref : str
            Repository reference (branch, commit, or tag) to download

        Returns:
        -------
        str
            Url from which to download a zip file

        Raises:
        ------
        ValueError
            if ref is not specified
        """
        if not ref:
            msg = "Missing either download ref (e.g. branch or commit) to generate Archive URL."
            LOGGER.error(msg)
            raise ValueError(msg)
        http_url = self._clean_http_url()
        remote_type = self._git_type()
        if remote_type == GitRemoteType.github:
            return f"{http_url}/archive/{ref}.zip"
        if remote_type == GitRemoteType.gitlab:
            return f"{http_url}/-/archive/{ref}/{self.name}.zip"
        return ""

    def get_file_raw_url(self, ref: str, file_path: str) -> str:
        """Gets the URL Pointing to the Raw file contents on the Remote.

        Parameters
        ----------
        ref : str
            Branch, Commit, Tag ,...
        file_path : str
            Relative file path in Repository

        Returns:
        -------
        str
            URL Pointing to the Raw file contents on the Remote
        """
        http_url = self._clean_http_url()
        remote_type = self._git_type()
        if remote_type == GitRemoteType.github:
            return f"{http_url.replace(self.domain, 'raw.githubusercontent.com')}/{ref}/{file_path}"
        if remote_type == GitRemoteType.gitlab:
            return f"{http_url}/-/raw/{ref}/{file_path}"
        return ""
