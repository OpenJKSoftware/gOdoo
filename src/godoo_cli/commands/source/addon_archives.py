"""Utilities for extracting third-party addon archives."""

import logging
import shutil
import tempfile
from pathlib import Path

from ...models import GodooModules

LOGGER = logging.getLogger(__name__)


def unpack_addon_archives(
    archive_folder: Path,
    target_addon_folder: Path,
    remove_excess: bool = False,
) -> None:
    """Extract zip archives from archive_folder into target_addon_folder."""
    target_addon_folder.mkdir(exist_ok=True, parents=True)
    if remove_excess:
        LOGGER.debug("Clearing out unarchive folder: %s", target_addon_folder)
        for folder in target_addon_folder.iterdir():
            shutil.rmtree(folder)

    LOGGER.info("Extracting archive addons to: %s", target_addon_folder)
    for zip_file in archive_folder.glob("*.zip"):
        LOGGER.info("Extracting addon archive: %s", zip_file)
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            shutil.unpack_archive(zip_file, td)
            # A zip may contain modules either at root or one level down.
            possible_paths = [td, *list(td.glob("*/"))]
            zip_modules = list(GodooModules(possible_paths).get_modules())
            if not zip_modules:
                LOGGER.warning("Could not find valid modules in thirdparty zip: %s", zip_file)
                continue

            LOGGER.debug(
                "Found modules in Zipfile:\n%s",
                [str(f.path.relative_to(td)) for f in zip_modules],
            )
            target_folder = target_addon_folder / ("single_mods" if len(zip_modules) == 1 else zip_file.stem)
            target_folder.mkdir(exist_ok=True)
            for module in zip_modules:
                module_target = target_folder / module.name
                shutil.rmtree(module_target, ignore_errors=True)
                shutil.move(module.path, module_target)
