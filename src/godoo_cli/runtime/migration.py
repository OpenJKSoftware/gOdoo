"""Resolve migration directories into existing runtime lifecycle inputs."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .lifecycle import split_lifecycle_values, split_upgrade_paths

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MigrationSelection:
    """Selected transition inputs, including inputs skipped by an empty update list."""

    directory: Path
    source_major: int
    target_major: int
    pre_upgrade_script: Path
    after_reconcile_dir: Path
    update_modules: tuple[str, ...]
    install_modules: tuple[str, ...]
    upgrade_paths: tuple[Path, ...]

    def plan_identity(self) -> dict[str, object]:
        """Keep the selected transition bound to resumable initialization."""
        return {
            "directory": str(self.directory),
            "source_major": self.source_major,
            "target_major": self.target_major,
            "pre_upgrade_script": str(self.pre_upgrade_script),
            "upgrade_roots": [str(path) for path in self.upgrade_paths],
        }


def _module_list(path: Path, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Parse transition lists with the existing lifecycle value rules."""
    if not path.exists():
        return default
    return tuple(split_lifecycle_values(path.read_text(encoding="utf-8").splitlines()))


def resolve_migration_directory(
    directory: Path,
    *,
    workspace_root: Path,
    additional_upgrade_paths: list[Path] | None = None,
) -> MigrationSelection:
    """Validate a transition directory without changing runtime state."""
    directory = directory.expanduser()
    directory = (workspace_root / directory).resolve() if not directory.is_absolute() else directory.resolve()
    if not directory.is_dir():
        message = f"Migration directory does not exist: {directory}"
        raise ValueError(message)
    versions = re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)", directory.name)
    if versions is None or int(versions[1]) >= int(versions[2]):
        message = f"Migration directory must use an increasing version pair, such as 16-19: {directory}"
        raise ValueError(message)
    pre_upgrade = directory / "pre-upgrade.py"
    if not pre_upgrade.is_file():
        message = f"Migration transition {directory.name} is missing pre-upgrade.py."
        raise ValueError(message)
    after_reconcile = directory / "after-reconcile"
    if not any(path.is_file() for path in after_reconcile.glob("*.py")):
        message = f"Migration transition {directory.name} requires direct after-reconcile/*.py hooks."
        raise ValueError(message)
    roots = [workspace_root / "migration" / "upgrade", directory / "upgrades"]
    selected_roots = split_upgrade_paths([path for path in roots if path.exists()] + (additional_upgrade_paths or []))
    for path in selected_roots:
        if not path.is_dir():
            message = f"Migration upgrade directory does not exist: {path}"
            raise ValueError(message)
    selection = MigrationSelection(
        directory=directory,
        source_major=int(versions[1]),
        target_major=int(versions[2]),
        pre_upgrade_script=pre_upgrade,
        after_reconcile_dir=after_reconcile,
        update_modules=_module_list(directory / "update_modules.txt", default=("all",)),
        install_modules=_module_list(directory / "install_modules.txt"),
        upgrade_paths=tuple(selected_roots),
    )
    LOGGER.debug("Selected migration transition %s", directory)
    return selection
