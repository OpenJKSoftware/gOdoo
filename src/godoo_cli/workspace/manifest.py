"""Model and parse the ``odoo_manifest.yml`` schema."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import unquote

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .specifications import GodooGitRepo


class ManifestError(ValueError):
    """Raised when a workspace manifest cannot be interpreted safely."""


def _manifest_fail(message: str) -> NoReturn:
    """Raise the manifest-specific validation error from one boundary."""
    raise ManifestError(message)


def _prefix(value: object) -> str:
    """Validate one third-party namespace before it can form any path or lock."""
    if not isinstance(value, str) or not value:
        message = "Third-party manifest prefixes must be nonempty strings."
        _manifest_fail(message)
    decoded = unquote(value)
    if (
        decoded != value
        or decoded in {".", ".."}
        or "/" in decoded
        or "\\" in decoded
        or (len(decoded) > 1 and decoded[1] == ":")
    ):
        message = f"Unsafe third-party manifest prefix: {value!r}"
        _manifest_fail(message)
    if len(Path(decoded).parts) != 1:
        message = f"Unsafe third-party manifest prefix: {value!r}"
        _manifest_fail(message)
    return decoded


def _repository(data: dict[object, object]) -> GodooGitRepo:
    """Build one repository while translating schema-value failures."""
    typed_data: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            message = "Repository manifest keys must be strings."
            _manifest_fail(message)
        typed_data[key] = value
    try:
        return GodooGitRepo.from_dict(typed_data)
    except (KeyError, TypeError, ValueError) as error:
        _manifest_fail(str(error))


@dataclass
class GodooManifest:
    """Represent the typed manifest data used by workspace resolution."""

    odoo: GodooGitRepo
    thirdparty: dict[str, list[GodooGitRepo]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject third-party path collisions before workspace resolution."""
        for prefix, repositories in self.thirdparty.items():
            names: set[str] = set()
            for repository in repositories:
                if repository.name in names:
                    message = f"Duplicate third-party repository name {repository.name!r} in prefix {prefix!r}."
                    _manifest_fail(message)
                names.add(repository.name)

    @property
    def default_branch(self) -> str:
        """Return the branch inherited by third-party repositories."""
        return self.odoo.branch or "master"

    def iter_thirdparty_repos(self) -> Iterator[tuple[str, GodooGitRepo]]:
        """Yield each third-party repository with its validated manifest prefix."""
        for prefix, repos in self.thirdparty.items():
            for repo in repos:
                yield prefix, repo

    _prefix = staticmethod(_prefix)

    @classmethod
    def from_yaml_file(cls, path: Path) -> GodooManifest:
        """Load and validate a typed manifest, normalizing malformed fields."""
        if not path.is_file():
            message = f"Manifest file not found: {path}"
            _manifest_fail(message)
        try:
            raw_data = YAML(typ="safe").load(path)
        except YAMLError as error:
            _manifest_fail(str(error))
        if not isinstance(raw_data, dict):
            message = f"Manifest must be a mapping: {path}"
            _manifest_fail(message)
        odoo_data = raw_data.get("odoo")
        if not isinstance(odoo_data, dict):
            message = f"Missing required 'odoo' mapping in manifest: {path}"
            _manifest_fail(message)
        thirdparty_data = raw_data.get("thirdparty", {})
        if not isinstance(thirdparty_data, dict):
            message = "Manifest field 'thirdparty' must be a mapping."
            _manifest_fail(message)
        thirdparty: dict[str, list[GodooGitRepo]] = {}
        for raw_prefix, repos in thirdparty_data.items():
            prefix = _prefix(raw_prefix)
            if not isinstance(repos, list):
                message = f"Third-party prefix {prefix!r} must contain a list of repositories."
                _manifest_fail(message)
            if not all(isinstance(repo, dict) for repo in repos):
                message = f"Third-party prefix {prefix!r} contains an invalid repository."
                _manifest_fail(message)
            thirdparty[prefix] = [_repository(repo) for repo in repos]
        return cls(odoo=_repository(odoo_data), thirdparty=thirdparty)
