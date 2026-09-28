"""Define shared gOdoo runtime and workspace settings."""

import logging
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from packaging.version import Version

from ..database.connection import DBConnection
from ..database.settings import DatabaseSettings
from ..workspace.manifest import GodooManifest
from .godoo_modules import GodooModules

LOGGER = logging.getLogger(__name__)


@dataclass(order=True)
class OdooVersion:
    """Store a parsed Odoo version."""

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
    manifest_path: Path | None = None
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
    """Collect paths and settings for one gOdoo runtime."""

    # Required fields (no defaults)
    odoo_install_folder: Path
    odoo_conf_path: Path
    workspace_addon_path: Path
    thirdparty_addon_path: Path

    # Optional fields
    manifest_path: Path | None = None
    resolved_addon_paths: tuple[Path, ...] | None = None

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
    db_sslmode: str | None = None

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
            db_sslmode=self.db_sslmode,
        )

    @cached_property
    def manifest(self) -> GodooManifest:
        """Return the parsed manifest file (cached).

        Raises:
            ValueError: If manifest_path is not configured.
        """
        from ..workspace.manifest import GodooManifest

        if not self.workspace_layout.manifest_path:
            msg = "manifest_path not configured in GodooConfig"
            raise ValueError(msg)
        return GodooManifest.from_yaml_file(self.workspace_layout.manifest_path)

    @cached_property
    def db_connection(self) -> DBConnection:
        """Return the cached database adapter for this configuration."""
        return self.database_settings.db_connection

    @property
    def zip_addon_path(self) -> Path:
        """Return the archived-addon directory."""
        return self.workspace_layout.zip_addon_path

    @property
    def odoo_bin_path(self) -> Path:
        """Return the ``odoo-bin`` path."""
        return self.workspace_layout.odoo_bin_path

    @cached_property
    def addon_paths(self) -> list[Path]:
        """Return explicit effective addon paths or discover workspace defaults."""
        if self.resolved_addon_paths is not None:
            return list(self.resolved_addon_paths)
        return AddonPathResolver(self.workspace_layout).resolve()
