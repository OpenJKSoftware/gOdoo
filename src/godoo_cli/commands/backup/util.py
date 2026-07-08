"""Utility functions for backup operations.

This module provides common utility functions used across backup operations,
including file transfer and system command execution.
"""

import logging
import subprocess
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def call_rsync(source_folder: Path, target_folder: Path, rsync_delete: bool = True):
    """Copy folder to another using Rsync.

    Args:
        source_folder: Source path to copy from.
        target_folder: Target path to copy to.
        rsync_delete: If True, delete files in target that don't exist in source.

    Raises:
        FileNotFoundError: If source folder doesn't exist or is empty.
    """
    if not source_folder.exists() or not any(source_folder.iterdir()):
        msg = f"Cannot find filestore Backup @ {source_folder}"
        LOGGER.error(msg)
        raise FileNotFoundError(msg)
    args = ["rsync", "-a", "--no-perms", "--no-owner", "--no-group", "--info=progress2"]
    if rsync_delete:
        args += ["--delete"]
    args += [f"{source_folder}/", f"{target_folder}/"]
    command = " ".join(map(str, args))
    LOGGER.debug("Running: %s", command)
    return subprocess.run(command, shell=True).returncode
