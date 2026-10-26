"""Copy filestores into disposable runtime staging directories."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LOGGER = logging.getLogger(__name__)
CLONE_PROBE_MAX_ENTRIES = 128


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


def _clone_probe_candidate(source: Path) -> Path | None:
    """Find a nonempty regular file without walking an entire filestore."""
    remaining_entries = CLONE_PROBE_MAX_ENTRIES

    def find_in(directory: Path) -> Path | None:
        nonlocal remaining_entries
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if remaining_entries == 0:
                        return None
                    remaining_entries -= 1
                    if entry.is_file(follow_symlinks=True) and entry.stat().st_size > 0:
                        return Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        candidate = find_in(Path(entry.path))
                        if candidate is not None:
                            return candidate
        except OSError:
            return None
        return None

    return find_in(source)


def _clone_preflight(source: Path, destination: Path) -> str | None:
    """Return why a cross-filesystem clone cannot be used, if known."""
    candidate = _clone_probe_candidate(source)
    if candidate is None:
        return f"no file found within the first {CLONE_PROBE_MAX_ENTRIES} filestore entries"

    probe = destination / ".godoo-filestore-clone-probe"
    command = ["cp", "-Lp", "--reflink=always", str(candidate), str(probe)]
    try:
        with tempfile.TemporaryFile(mode="w+") as diagnostics:
            result = subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=diagnostics, text=True)
            diagnostics.seek(0, 2)
            diagnostics.seek(max(0, diagnostics.tell() - 4096))
            detail = diagnostics.read().strip()
    except OSError as error:
        return f"cp could not start: {error}"
    finally:
        probe.unlink(missing_ok=True)

    if result.returncode == 0:
        return None
    message = f"cp exited with status {result.returncode}"
    if detail:
        message += f": {detail}"
    return message


def _copy_with_rsync(source: Path, destination: Path) -> tuple[bool, str | None]:
    """Copy one staging filestore with rsync output going directly to stdout."""
    if not shutil.which("rsync"):
        return False, "rsync is not available"

    command = ["rsync", "-rLpt", "--human-readable"]
    if sys.stdout.isatty():
        command.append("--info=progress2")
    command.extend([f"{source}/", str(destination)])
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
        if effective_platform.startswith("linux"):
            LOGGER.info("Checking whether filestore cloning is available")
            fast_copy_error = _clone_preflight(source, destination)
            if fast_copy_error:
                LOGGER.info("Filestore cloning is unavailable (%s); using rsync", fast_copy_error)
            else:
                LOGGER.info("Filestore clone preflight succeeded; copying with reflink")
        if fast_copy_error is None:
            try:
                with tempfile.TemporaryFile(mode="w+") as diagnostics:
                    result = subprocess.run(
                        command, check=False, stdout=subprocess.DEVNULL, stderr=diagnostics, text=True
                    )
                    diagnostics.seek(0, 2)
                    diagnostics.seek(max(0, diagnostics.tell() - 4096))
                    detail = diagnostics.read().strip()
            except OSError as error:
                fast_copy_error = f"{command[0]} could not start: {error}"
            else:
                if result.returncode == 0:
                    LOGGER.info("Copied filestore with %s reflink", platform_name or sys.platform)
                    return
                fast_copy_error = f"{command[0]} exited with status {result.returncode}"
                if detail:
                    fast_copy_error += f": {detail}"
            LOGGER.info("Filestore clone copy failed (%s); removing partial stage", fast_copy_error)
            # A partially copied directory is safe to discard because this helper
            # only accepts a new staging path that has not been promoted.
            try:
                shutil.rmtree(destination)
                destination.mkdir()
            except OSError as error:
                message = f"{fast_copy_error}; could not remove partial stage {destination}: {error}"
                raise RuntimeError(message) from error
            LOGGER.info("Removed partial filestore stage; using rsync")
    elif command is not None:
        fast_copy_error = f"{command[0]} is not available"

    if effective_platform.startswith("linux"):
        LOGGER.info("Copying filestore with rsync")
        copied, rsync_error = _copy_with_rsync(source, destination)
        if copied:
            LOGGER.info("Copied filestore with rsync")
            return
        if rsync_error:
            LOGGER.info("rsync filestore copy failed (%s); using Python copy fallback", rsync_error)
            try:
                shutil.rmtree(destination)
                destination.mkdir()
            except OSError as error:
                message = f"{rsync_error}; could not remove partial stage {destination}: {error}"
                raise RuntimeError(message) from error
    else:
        LOGGER.info("Copying filestore with Python fallback")
    try:
        shutil.copytree(source, destination, symlinks=False, dirs_exist_ok=destination.exists())
    except Exception as error:
        if fast_copy_error:
            message = f"Fast filestore copy failed ({fast_copy_error}); Python copy fallback failed: {error}"
            raise RuntimeError(message) from error
        raise
    LOGGER.info("Copied filestore with Python fallback")
