"""Typed dataclasses for odoo_manifest.yml schema.

This module provides a typed representation of the manifest file that defines
Odoo and third-party addon repositories. It centralizes YAML handling and
provides a single source of truth for the manifest schema.
"""

import logging
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .godoo_git_repo import GodooGitRepo

LOGGER = logging.getLogger(__name__)


def _yaml_roundtrip_loader() -> YAML:
    """Create a YAML loader that preserves comments and formatting.

    Returns:
        Configured ruamel.yaml instance for roundtrip loading/saving.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    return yaml


@dataclass
class GodooManifest:
    """Typed representation of odoo_manifest.yml.

    This class provides a structured interface to the manifest file,
    replacing raw dict access with typed properties and methods.

    Attributes:
        odoo: Main Odoo repository specification.
        thirdparty: Third-party addon repositories, keyed by prefix (e.g., 'OCA').
        _source_path: Path to the source YAML file (for save operations).
        _raw_data: Raw YAML data to preserve comments on save.
    """

    odoo: GodooGitRepo
    thirdparty: dict[str, list[GodooGitRepo]] = field(default_factory=dict)
    _source_path: Optional[Path] = field(default=None, repr=False)
    _raw_data: Optional[CommentedMap] = field(default=None, repr=False)

    @property
    def default_branch(self) -> str:
        """Default branch from Odoo repo (used for thirdparty repos without explicit branch)."""
        return self.odoo.branch or "master"

    def iter_thirdparty_repos(self) -> Iterator[tuple[str, GodooGitRepo]]:
        """Iterate over all third-party repositories with their prefixes.

        Yields:
            Tuple of (prefix, repo_spec) for each third-party repository.
        """
        for prefix, repos in self.thirdparty.items():
            for repo in repos:
                yield prefix, repo

    def remove_unused_addon_folders(
        self,
        thirdparty_addon_path: Path,
        keep_folders: Optional[list[Path]] = None,
    ) -> list[Path]:
        """Remove addon folders not specified in the manifest.

        Args:
            thirdparty_addon_path: Root folder containing third-party addon repositories.
            keep_folders: Additional folders to keep regardless of manifest.

        Returns:
            List of removed folder paths.
        """
        keep_folders = keep_folders or []
        keep_folders_absolute = {p.absolute() for p in keep_folders}
        allowed_folders = {f"{prefix}_{repo.name}" for prefix, repo in self.iter_thirdparty_repos()}
        removed: list[Path] = []

        for folder in thirdparty_addon_path.iterdir():
            if not folder.is_dir():
                continue
            if folder.absolute() in keep_folders_absolute:
                continue
            if folder.stem not in allowed_folders:
                LOGGER.info("Removing unspecified Addon Folder: %s", folder)
                shutil.rmtree(folder)
                removed.append(folder)

        return removed

    @classmethod
    def from_yaml_file(cls, path: Path) -> "GodooManifest":
        """Load manifest from a YAML file.

        Args:
            path: Path to the manifest YAML file.

        Returns:
            Parsed manifest instance.

        Raises:
            FileNotFoundError: If the manifest file doesn't exist.
            KeyError: If required 'odoo' section is missing.
        """
        if not path.exists():
            msg = f"Manifest file not found: {path}"
            LOGGER.error(msg)
            raise FileNotFoundError(msg)

        yaml = _yaml_roundtrip_loader()
        raw_data: CommentedMap = yaml.load(path)

        if not raw_data:
            msg = f"Empty or invalid manifest file: {path}"
            LOGGER.error(msg)
            raise ValueError(msg)

        odoo_data = raw_data.get("odoo")
        if not odoo_data:
            msg = f"Missing required 'odoo' section in manifest: {path}"
            LOGGER.error(msg)
            raise KeyError(msg)

        odoo_spec = GodooGitRepo.from_dict(odoo_data)

        thirdparty: dict[str, list[GodooGitRepo]] = {}
        if thirdparty_data := raw_data.get("thirdparty"):
            for prefix, repos in thirdparty_data.items():
                thirdparty[prefix] = [GodooGitRepo.from_dict(r) for r in repos]

        return cls(
            odoo=odoo_spec,
            thirdparty=thirdparty,
            _source_path=path,
            _raw_data=raw_data,
        )

    def to_yaml_file(
        self,
        path: Optional[Path] = None,
        add_compare_urls: bool = False,
    ) -> None:
        """Save manifest to a YAML file, preserving comments where possible.

        Args:
            path: Target path. If None, uses the source path.
            add_compare_urls: Whether to add compare URL comments for pinned commits.

        Raises:
            ValueError: If no path is provided and no source path exists.
        """
        path = path or self._source_path
        if not path:
            msg = "No path specified and no source path available"
            LOGGER.error(msg)
            raise ValueError(msg)

        yaml = _yaml_roundtrip_loader()
        data = self._build_yaml_data(add_compare_urls)
        yaml.dump(data, path)
        LOGGER.debug("Saved manifest to: %s", path)

    def _build_yaml_data(self, add_compare_urls: bool) -> CommentedMap:
        """Build YAML data structure, preserving comments if available."""
        if self._raw_data is not None:
            return self._update_raw_yaml_data(add_compare_urls)
        return self._build_fresh_yaml_data(add_compare_urls)

    def _update_raw_yaml_data(self, add_compare_urls: bool) -> CommentedMap:
        """Update existing raw YAML data with current values."""
        data = self._raw_data
        odoo_section = data.get("odoo")
        data["odoo"] = self.odoo.update_yaml_node(odoo_section, add_compare_urls, self.default_branch)
        if self.thirdparty:
            self._update_thirdparty_section(data, add_compare_urls)
        elif "thirdparty" in data:
            del data["thirdparty"]
        return data

    def _update_thirdparty_section(self, data: CommentedMap, add_compare_urls: bool) -> None:
        """Update the thirdparty section in YAML data."""
        if "thirdparty" not in data:
            data["thirdparty"] = CommentedMap()

        tp_map = data["thirdparty"]
        current_prefixes = set(tp_map.keys())
        desired_prefixes = set(self.thirdparty.keys())

        for removed_prefix in current_prefixes - desired_prefixes:
            del tp_map[removed_prefix]

        for prefix, repos in self.thirdparty.items():
            repo_list = tp_map.get(prefix)
            if repo_list is None:
                repo_list = []
                tp_map[prefix] = repo_list

            while len(repo_list) > len(repos):
                del repo_list[-1]

            for idx, repo in enumerate(repos):
                if idx < len(repo_list):
                    repo_list[idx] = repo.update_yaml_node(
                        repo_list[idx],
                        add_compare_urls,
                        self.default_branch,
                    )
                else:
                    repo_list.append(repo.update_yaml_node(None, add_compare_urls, self.default_branch))

    def _build_fresh_yaml_data(self, add_compare_urls: bool) -> CommentedMap:
        """Build fresh YAML data structure from scratch."""
        data = CommentedMap()
        data["odoo"] = self.odoo.update_yaml_node(None, add_compare_urls, self.default_branch)
        if self.thirdparty:
            data["thirdparty"] = CommentedMap()
            for prefix, repos in self.thirdparty.items():
                data["thirdparty"][prefix] = [
                    repo.update_yaml_node(None, add_compare_urls, self.default_branch) for repo in repos
                ]
        return data
