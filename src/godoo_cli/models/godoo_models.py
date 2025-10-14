"""Models For general Godoo Settings."""

from dataclasses import dataclass
from functools import cached_property  # <-- Add this import
from pathlib import Path
from typing import Optional

from .db_connection import DBConnection
from .godoo_modules import GodooModules


@dataclass
class OdooVersion:
    """Structure to hold Odoo version."""

    text: str
    major: int
    minor: int

    @property
    def raw(self):
        """Return the version number in major.minor format."""
        return f"{self.major}.{self.minor}"


@dataclass
class GodooConfig:
    """Structure to hold Essential values for Godoo."""

    odoo_install_folder: Optional[Path] = None
    odoo_conf_path: Optional[Path] = None
    workspace_addon_path: Optional[Path] = None
    thirdparty_addon_path: Optional[Path] = None

    multithread_worker_count: int = -1  # -1 is treated as autodetect
    languages: str = "de_DE,en_US"

    db_user: str = ""
    db_password: str = ""
    db_host: str = ""
    db_port: int = 0
    db_name: str = ""

    db_filter: str = ""

    @cached_property
    def db_connection(self) -> DBConnection:
        """Return a DBConnection object based on the configuration (cached)."""
        return DBConnection(
            hostname=self.db_host,
            port=self.db_port,
            username=self.db_user,
            password=self.db_password,
            db_name=self.db_name,
        )

    @property
    def zip_addon_path(self) -> Path:
        """Return the path to the zip addons folder."""
        return self.thirdparty_addon_path / "custom"

    @property
    def odoo_bin_path(self) -> Path:
        """Return the path to the odoo-bin file."""
        return self.odoo_install_folder / "odoo-bin"

    @cached_property
    def odoo_version(self) -> OdooVersion:
        """Return the Odoo version (cached)."""
        from ..helpers.odoo_files import odoo_bin_get_version

        return odoo_bin_get_version(self.odoo_install_folder)

    @cached_property
    def addon_paths(self) -> list[Path]:
        """Get all valid Odoo addon paths for the odoo.conf addons_path setting.

        This function collects all valid addon paths from:
        - Core Odoo addons (from odoo_main_repo)
        - Workspace addons (if any valid modules exist)
        - Thirdparty addons (both zip-based and git-based)

        The function validates each path to ensure it contains valid Odoo modules
        before including it in the result.

        Returns:
            List[Path]: List of unique, valid addon paths that should be included
                in the Odoo addons_path configuration.
        """
        odoo_addon_paths = [self.odoo_install_folder / "addons", self.odoo_install_folder / "odoo" / "addons"]
        if next(GodooModules(self.workspace_addon_path).get_modules(), None):
            odoo_addon_paths.append(self.workspace_addon_path)
        zip_addon_path = self.zip_addon_path
        zip_addon_repos = [
            f for f in zip_addon_path.iterdir() if f.is_dir() and next(GodooModules(f).get_modules(), None)
        ]
        odoo_addon_paths += zip_addon_repos
        git_thirdparty_addon_repos = [
            p for p in self.thirdparty_addon_path.iterdir() if next(GodooModules(p).get_modules(), None)
        ]
        odoo_addon_paths += git_thirdparty_addon_repos
        return list(set(odoo_addon_paths))
