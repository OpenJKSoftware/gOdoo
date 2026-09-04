"""Parse Git URLs and derive hosting-service URLs."""

import re
from enum import Enum
from logging import getLogger
from typing import Literal, Optional

LOGGER = getLogger(__name__)


class GitRemoteType(Enum):
    """Identify a supported Git hosting service."""

    gitlab = "gitlab"
    github = "github"


class GitUrl:
    """Parse local, HTTP(S), and SSH repository locations.

    Attributes:
        url: The original repository location.
        url_type: The location scheme: file, HTTP, HTTPS, or SSH.
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
        """Parse a repository location.

        Args:
            url: A local path, file URL, HTTP(S) URL, or SSH URL.

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
        """Return an HTTPS repository URL without its ``.git`` suffix."""
        return f"https://{self.domain}/{self.path}"

    def _git_type(self) -> GitRemoteType:
        """Return the supported Git hosting service."""
        if "gitlab" in self.domain:
            return GitRemoteType.gitlab
        if "github" in self.domain:
            return GitRemoteType.github
        msg = f"Cant get Git Service type from {self.domain}"
        LOGGER.error(msg)
        raise ValueError(msg)

    def get_compare_url(self, from_compare: str, to_compare: str) -> str:
        """Build a compare URL between two refs."""
        remote_type = self._git_type()
        if from_compare == to_compare:
            return ""  # Nothing to Compare here
        http_url = self._clean_http_url()
        if remote_type in [GitRemoteType.github, GitRemoteType.gitlab]:
            return f"{http_url}/compare/{from_compare}...{to_compare}"
        return ""

    def get_archive_url(self, ref: str) -> str:
        """Build a ZIP archive URL for a ref."""
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
        """Build a raw-file URL for a ref and repository path."""
        http_url = self._clean_http_url()
        remote_type = self._git_type()
        if remote_type == GitRemoteType.github:
            return f"{http_url.replace(self.domain, 'raw.githubusercontent.com')}/{ref}/{file_path}"
        if remote_type == GitRemoteType.gitlab:
            return f"{http_url}/-/raw/{ref}/{file_path}"
        return ""
