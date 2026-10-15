"""Copy filestores into disposable runtime staging directories."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
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
        return ["cp", "-RLp", "--reflink=always", f"{source}/.", str(destination)]
    return None


def _copy_with_rsync(source: Path, destination: Path) -> tuple[bool, str | None]:
    """Copy one staging filestore with rsync output going directly to stdout."""
    if not shutil.which("rsync"):
        return False, "rsync is not available"

    command = ["rsync", "-rLpt", "--human-readable", "--info=progress2", f"{source}/", str(destination)]
    try:
        process = subprocess.Popen(command, stdout=None, stderr=subprocess.STDOUT)
    except OSError as error:
        return False, f"rsync could not start: {error}"

    try:
        if process.wait() == 0:
            return True, None
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return False, "rsync failed while copying the filestore"


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
    effective_platform = platform_name or sys.platform
    command = _copy_command(source, destination, effective_platform)
    fast_copy_error: str | None = None
    if command is not None and shutil.which(command[0]):
        try:
            with tempfile.TemporaryFile(mode="w+") as diagnostics:
                result = subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=diagnostics, text=True)
                diagnostics.seek(0, 2)
                diagnostics.seek(max(0, diagnostics.tell() - 4096))
                detail = diagnostics.read().strip()
        except OSError as error:
            fast_copy_error = f"{command[0]} could not start: {error}"
        else:
            if result.returncode == 0:
                LOGGER.debug("Copied filestore with %s fast path", platform_name or sys.platform)
                return
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
    if effective_platform.startswith("linux"):
        copied, rsync_error = _copy_with_rsync(source, destination)
        if copied:
            return
        if rsync_error:
            LOGGER.debug("Measured rsync filestore copy unavailable (%s); using Python copy fallback", rsync_error)
            try:
                shutil.rmtree(destination)
                destination.mkdir()
            except OSError as error:
                message = f"{rsync_error}; could not remove partial stage {destination}: {error}"
                raise RuntimeError(message) from error
    try:
        shutil.copytree(source, destination, symlinks=False, dirs_exist_ok=destination.exists())
    except Exception as error:
        if fast_copy_error:
            message = f"Fast filestore copy failed ({fast_copy_error}); Python copy fallback failed: {error}"
            raise RuntimeError(message) from error
        raise
