"""Copy filestores into disposable runtime staging directories."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def _copy_command(source: Path, destination: Path, platform_name: str) -> list[str] | None:
    """Choose the platform copy command and options."""
    if platform_name == "darwin":
        children = sorted(source.iterdir())
        if not children:
            return None
        return ["cp", "-cRLp", *(str(path) for path in children), str(destination)]
    if platform_name.startswith("linux"):
        # Keep the literal `/.` operand: Path normalizes it away, and GNU cp
        # would then nest the source directory beneath the staging directory.
        return ["cp", "-RLp", "--reflink=auto", f"{source}/.", str(destination)]
    return None


# Keep staging creation, fast-copy fallback, and partial-stage cleanup together.
def copy_filestore(  # noqa: C901
    source: Path,
    destination: Path,
    *,
    platform_name: str | None = None,
) -> None:
    """Copy a filestore into a fresh staging directory as independent files."""
    if not source.is_dir():
        message = f"Filestore directory does not exist: {source}"
        raise FileNotFoundError(message)
    if destination.exists():
        message = f"Filestore staging path already exists: {destination}"
        raise FileExistsError(message)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    command = _copy_command(source, destination, platform_name or sys.platform)
    fast_copy_error: str | None = None
    if command is not None and shutil.which(command[0]):
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
        except OSError as error:
            fast_copy_error = f"{command[0]} could not start: {error}"
        else:
            if result.returncode == 0:
                LOGGER.debug("Copied filestore with %s fast path", platform_name or sys.platform)
                return
            detail = result.stderr.strip() if result.stderr else ""
            fast_copy_error = f"{command[0]} exited with status {result.returncode}"
            if detail:
                fast_copy_error += f": {detail}"
        # A partially copied directory is safe to discard because this helper
        # only accepts a new staging path that has not been promoted.
        try:
            shutil.rmtree(destination)
        except OSError as error:
            message = f"{fast_copy_error}; could not remove partial stage {destination}: {error}"
            raise RuntimeError(message) from error
    elif command is not None:
        fast_copy_error = f"{command[0]} is not available"

    if fast_copy_error:
        LOGGER.debug("Fast filestore copy failed (%s); using Python copy fallback", fast_copy_error)
    else:
        LOGGER.debug("Using Python filestore copy fallback")
    try:
        shutil.copytree(source, destination, symlinks=False, dirs_exist_ok=destination.exists())
    except Exception as error:
        if fast_copy_error:
            message = f"Fast filestore copy failed ({fast_copy_error}); Python copy fallback failed: {error}"
            raise RuntimeError(message) from error
        raise
