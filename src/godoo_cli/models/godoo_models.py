"""Models For general Godoo Settings."""

import logging
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Optional

from packaging.version import Version

from .db_connection import DBConnection
from .godoo_manifest import GodooManifest
from .godoo_modules import GodooModules

LOGGER = logging.getLogger(__name__)


@dataclass(order=True)
class OdooVersion:
    """Structure to hold Odoo version."""

    text: str = field(compare=False)
    major: int
    minor: int

    @property
    def raw(self) -> str:
        """Return the version number in major.minor format."""
        return f"{self.major}.{self.minor}"

    @property
    def semantic(self) -> Version:
        """Return the normalized, comparable semantic version."""
        return Version(self.raw)


@dataclass(frozen=True)
class WorkspaceLayout:
    """Immutable filesystem layout for one gOdoo workspace.

    This object deliberately contains only paths. It can therefore be shared by
    lifecycle operations without coupling them to database credentials or
    command-specific settings.
    """

    odoo_install_folder: Path
    odoo_conf_path: Path
    workspace_addon_path: Path
    thirdparty_addon_path: Path
    manifest_path: Optional[Path] = None
    data_dir: Path = Path("/var/lib/odoo")

    @property
    def zip_addon_path(self) -> Path:
        """Return the directory containing archived third-party addons."""
        return self.thirdparty_addon_path / "custom"

    @property
    def odoo_bin_path(self) -> Path:
        """Return the odoo-bin executable path for this installation."""
        return self.odoo_install_folder / "odoo-bin"


@dataclass(frozen=True)
class DatabaseSettings:
    """Immutable database identity used by Odoo and gOdoo operations."""

    db_user: str = ""
    db_password: str = ""
    db_host: str = ""
    db_port: int = 0
    db_name: str = ""
    db_filter: str = ""

    @cached_property
    def db_connection(self) -> DBConnection:
        """Return the DBConnection adapter for these settings."""
        return DBConnection(
            hostname=self.db_host,
            port=self.db_port,
            username=self.db_user,
            password=self.db_password,
            db_name=self.db_name,
        )


@dataclass(frozen=True)
class AddonPathResolver:
    """Discover valid Odoo addon repositories for a workspace layout."""

    workspace_layout: WorkspaceLayout

    def resolve(self) -> list[Path]:
        """Return stable, unique addon paths containing valid Odoo modules."""
        layout = self.workspace_layout
        addon_paths = [
            path
            for path in (layout.odoo_install_folder / "addons", layout.odoo_install_folder / "odoo" / "addons")
            if path.is_dir()
        ]
        if layout.workspace_addon_path.is_dir() and self._contains_modules(layout.workspace_addon_path):
            addon_paths.append(layout.workspace_addon_path)

        zip_addon_path = layout.zip_addon_path
        if zip_addon_path.is_dir():
            addon_paths.extend(path for path in sorted(zip_addon_path.iterdir()) if self._is_addon_repository(path))
        if layout.thirdparty_addon_path.is_dir():
            addon_paths.extend(
                path
                for path in sorted(layout.thirdparty_addon_path.iterdir())
                if path != zip_addon_path and self._is_addon_repository(path)
            )
        return list(dict.fromkeys(addon_paths))

    @staticmethod
    def _contains_modules(path: Path) -> bool:
        """Return whether an addon root contains at least one valid module."""
        return next(GodooModules(path).get_modules(), None) is not None

    @classmethod
    def _is_addon_repository(cls, path: Path) -> bool:
        """Return whether a repository directory contains valid Odoo modules."""
        return path.is_dir() and cls._contains_modules(path)


@dataclass(frozen=True)
class GodooConfig:
    """Structure to hold Essential values for Godoo.

    Required fields (must be provided at instantiation):
    - odoo_install_folder: Path to the Odoo installation
    - odoo_conf_path: Path to odoo.conf
    - workspace_addon_path: Path to workspace addons
    - thirdparty_addon_path: Path to third-party addons

    Optional and configurable fields have defaults.
    """

    # Required fields (no defaults)
    odoo_install_folder: Path
    odoo_conf_path: Path
    workspace_addon_path: Path
    thirdparty_addon_path: Path

    # Optional fields
    manifest_path: Optional[Path] = None

    # Configurable fields with defaults
    data_dir: Path = Path("/var/lib/odoo")
    multithread_worker_count: int = -1  # -1 is treated as autodetect
    languages: str = "de_DE,en_US"

    # Database connection fields with defaults
    db_user: str = ""
    db_password: str = ""
    db_host: str = ""
    db_port: int = 0
    db_name: str = ""
    db_filter: str = ""

    @cached_property
    def workspace_layout(self) -> WorkspaceLayout:
        """Return the immutable filesystem layout represented by this config."""
        return WorkspaceLayout(
            odoo_install_folder=self.odoo_install_folder,
            odoo_conf_path=self.odoo_conf_path,
            workspace_addon_path=self.workspace_addon_path,
            thirdparty_addon_path=self.thirdparty_addon_path,
            manifest_path=self.manifest_path,
            data_dir=self.data_dir,
        )

    @cached_property
    def database_settings(self) -> DatabaseSettings:
        """Return the immutable database settings represented by this config."""
        return DatabaseSettings(
            db_user=self.db_user,
            db_password=self.db_password,
            db_host=self.db_host,
            db_port=self.db_port,
            db_name=self.db_name,
            db_filter=self.db_filter,
        )

    @cached_property
    def manifest(self) -> GodooManifest:
        """Return the parsed manifest file (cached).

        Raises:
            ValueError: If manifest_path is not configured.
        """
        from .godoo_manifest import GodooManifest

        if not self.workspace_layout.manifest_path:
            msg = "manifest_path not configured in GodooConfig"
            raise ValueError(msg)
        return GodooManifest.from_yaml_file(self.workspace_layout.manifest_path)

    @cached_property
    def db_connection(self) -> DBConnection:
        """Return a DBConnection object based on the configuration (cached)."""
        return self.database_settings.db_connection

    @property
    def zip_addon_path(self) -> Path:
        """Return the path to the zip addons folder."""
        return self.workspace_layout.zip_addon_path

    @property
    def odoo_bin_path(self) -> Path:
        """Return the path to the odoo-bin file."""
        return self.workspace_layout.odoo_bin_path

    @cached_property
    def odoo_version(self) -> OdooVersion:
        """Return the Odoo version (cached)."""
        from ..helpers.odoo_files import odoo_bin_get_version

        return odoo_bin_get_version(self.odoo_install_folder)

    @cached_property
    def addon_paths(self) -> list[Path]:
        """Return discovered addon paths through the workspace resolver.

        Kept as a compatibility property while discovery remains an explicit,
        reusable concern in :class:`AddonPathResolver`.
        """
        return AddonPathResolver(self.workspace_layout).resolve()
