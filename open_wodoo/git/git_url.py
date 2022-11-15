import re
from typing import Literal


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
