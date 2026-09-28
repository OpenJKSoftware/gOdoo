"""Parse, canonicalize, and transport Git repository URLs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit


class GitUrl:
    """One authoritative Git URL identity and transport representation."""

    url: str
    url_type: Literal["file", "http", "https", "ssh"]
    domain: str
    path: str
    user: str
    port: int | None
    name: str

    def __init__(self, url: str) -> None:
        """Parse and validate one native or legacy Git transport URL."""
        self.url = url
        native = self._legacy_transport(url)
        if native.startswith("file://") or Path(native).is_absolute():
            parsed = urlsplit(native)
            if parsed.scheme == "file" and parsed.netloc not in {"", "localhost"}:
                message = "File repository URLs must refer to the local host."
                raise ValueError(message)
            self.url_type = "file"
            self.domain = ""
            self.path = str(Path(unquote(parsed.path) if parsed.scheme else native).expanduser().resolve())
            self.user = ""
            self.port = None
        elif native.startswith(("http://", "https://", "ssh://")):
            parsed = urlsplit(native)
            if not parsed.hostname or not parsed.path or parsed.password or parsed.query or parsed.fragment:
                message = "Repository URLs need a host and path and must not contain credentials or query strings."
                raise ValueError(message)
            if parsed.username and parsed.scheme != "ssh":
                message = "Repository URLs must not contain credentials; use a Git credential helper or SSH."
                raise ValueError(message)
            self.url_type = parsed.scheme  # type: ignore[assignment]
            self.domain = parsed.hostname.lower()
            self.path = unquote(parsed.path)
            self.user = parsed.username or ""
            self.port = parsed.port
        else:
            match = re.fullmatch(r"(?:(?P<user>[^/@:]+)@)?(?P<host>[^/:]+):(?P<path>.+)", native)
            if not match:
                message = "Invalid Git repository URL."
                raise ValueError(message)
            self.url_type = "ssh"
            self.domain = match["host"].lower()
            self.path = match["path"]
            self.user = match["user"] or ""
            self.port = None
        self.path = self.path.rstrip("/").removesuffix(".git")
        if self.url_type != "file":
            self.path = self.path.lstrip("/")
        if not self.path:
            message = "Repository URLs need a repository path."
            raise ValueError(message)
        self.name = self.path.split("/")[-1]

    @staticmethod
    def _legacy_transport(url: str) -> str:
        match = re.fullmatch(r"\[([^@\[\]]+)@([^:\[\]]+):(\d+)\]:(.+)", url)
        if match:
            user, host, port, path = match.groups()
            return f"ssh://{user}@{host}:{port}/{path.lstrip('/')}"
        return url

    @property
    def transport(self) -> str:
        """Return the URL passed to Git without legacy bracket syntax."""
        return self._legacy_transport(self.url)

    @property
    def canonical(self) -> str:
        """Return the credential-free identity used for pools and recipes."""
        if self.url_type == "file":
            return Path(self.path).as_uri()
        if self.domain in {"github.com", "gitlab.com", "bitbucket.org"} and self.port in {None, 22, 443}:
            return f"https://{self.domain}/{self.path}"
        if "://" not in self.transport:
            authority = f"{self.user}@" if self.user else ""
            return f"{authority}{self.domain}:{self.path}"
        authority = f"{self.user}@" if self.user and self.url_type == "ssh" else ""
        authority += self.domain
        if self.port is not None and (self.url_type, self.port) not in {("https", 443), ("http", 80), ("ssh", 22)}:
            authority += f":{self.port}"
        return f"{self.url_type}://{authority}/{self.path}"
