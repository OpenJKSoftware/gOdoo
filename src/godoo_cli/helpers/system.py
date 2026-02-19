"""Helper functions around the host system."""

import atexit
import contextlib
import datetime
import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional, Union

import click
import requests
from rich.console import Console
from rich.logging import RichHandler
from rich.prompt import Confirm
from rich.table import Table
from rich.traceback import install as install_rich_traceback

from . import cli as godoo_cli_helpers

LOGGER = logging.getLogger(__name__)


class RegexLogFilter(logging.Filter):
    """A logging filter that only passes records whose logger name matches a regex pattern."""

    def __init__(self, pattern: str) -> None:
        """Compile and store the regex pattern used for filtering."""
        super().__init__()
        self._pattern = re.compile(pattern)

    def filter(self, record: logging.LogRecord) -> bool:
        """Return True only if the record's logger name matches the pattern."""
        return bool(self._pattern.search(record.name))


def _filter_thread_main(pattern: re.Pattern, r_fd: int, orig_fd: int) -> None:
    """Read lines from r_fd and write only those matching pattern to orig_fd.

    Lines that are part of a traceback block (starting with ``Traceback``) are
    always emitted in full, regardless of the primary pattern, so that error
    context is never silently dropped.
    """
    traceback_start_re = re.compile(rb"^Traceback ")
    in_traceback = False

    def should_emit(line_bytes: bytes) -> bool:
        nonlocal in_traceback
        if in_traceback:
            # Tracebacks are delimited by an empty line; emit it, then leave traceback mode.
            if not line_bytes.strip():
                in_traceback = False
            return True
        if traceback_start_re.match(line_bytes):
            in_traceback = True
            return True
        return bool(pattern.search(line_bytes.decode("utf-8", errors="replace")))

    buf = b""
    with os.fdopen(r_fd, "rb") as reader, os.fdopen(orig_fd, "wb") as writer:
        while True:
            chunk = reader.read(4096)
            if not chunk:
                # EOF - flush any unterminated line
                if buf and should_emit(buf):
                    writer.write(buf)
                    writer.flush()
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if should_emit(line):
                    writer.write(line + b"\n")
                    writer.flush()


def _install_fd_filter(pattern: re.Pattern) -> None:
    """Redirect fd 1 and fd 2 through a pipe and filter each line by regex.

    Saves the original stdout fd, creates a pipe, points fd 1 and fd 2 at the
    pipe's write end, then starts a thread that reads from the read end
    and forwards only lines matching *pattern* to the original stdout fd.
    This intercepts output from both Python code and child subprocesses.
    An atexit handler closes the write end and joins the thread so the last
    lines are never lost.
    """
    orig_fd = os.dup(sys.stdout.fileno())  # save original stdout (e.g. fd 1 -> terminal)
    r_fd, w_fd = os.pipe()
    os.dup2(w_fd, 1)  # fd 1 now -> pipe write end
    os.dup2(w_fd, 2)  # fd 2 now -> pipe write end
    os.close(w_fd)

    # Rebind Python's sys.stdout/stderr so buffered I/O goes to the new fd too
    sys.stdout = os.fdopen(1, "w", buffering=1, closefd=False)
    sys.stderr = sys.stdout

    t = threading.Thread(
        target=_filter_thread_main,
        args=(pattern, r_fd, orig_fd),
        daemon=True,
        name="godoo-log-filter",
    )
    t.start()

    def _cleanup() -> None:
        # Flush Python-buffered output into the pipe, then close the write end.
        # Closing fd 1 AND fd 2 sends EOF to the reader thread, letting it drain and exit.
        with contextlib.suppress(Exception):
            sys.stdout.flush()
        for fd in (1, 2):
            with contextlib.suppress(OSError):
                os.close(fd)
        t.join(timeout=10)

    atexit.register(_cleanup)


def run_cmd(command: str, **kwargs: dict[str, Any]) -> subprocess.CompletedProcess:
    """Runs command via subprocess.run."""
    LOGGER.debug("Running shell:\n%s", command)
    if not kwargs.get("shell"):
        kwargs["shell"] = True
    proc = subprocess.run(command, **kwargs)
    LOGGER.debug("Return Code: %s", proc.returncode)
    return proc


def ensure_dotenv(varname: str) -> str:
    """Load environment variable and raise error if not set."""
    var = os.getenv(varname)
    if var is None:
        msg = f"Env Variable: {varname} is not set"
        LOGGER.error(msg)
        raise ReferenceError(msg)
    return var


def set_logging(verbose: bool = False, log_filter: Optional[str] = None) -> None:
    """Set the logging configuration according to passed arguments.

    Args:
        verbose: Enable DEBUG-level logging with rich tracebacks.
        log_filter: Optional regex pattern; only log records whose logger name
            matches the pattern will be emitted.
    """
    if verbose:
        install_rich_traceback(suppress=[click, godoo_cli_helpers])
        logging.basicConfig(
            level=logging.DEBUG,
            format="[italic bright_black]{name}:[/] {message}",
            style="{",
            handlers=[
                RichHandler(
                    level=logging.DEBUG,
                    markup=True,
                    show_path=True,
                    rich_tracebacks=True,
                    tracebacks_show_locals=True,
                )
            ],
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="{message}",
            style="{",
            handlers=[RichHandler(level=logging.INFO, show_path=False, rich_tracebacks=False)],
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    if log_filter:
        pattern = re.compile(log_filter)
        regex_filter = RegexLogFilter(log_filter)
        for handler in logging.root.handlers:
            handler.addFilter(regex_filter)
        _install_fd_filter(pattern)
        LOGGER.debug("Log filter active: '%s'", log_filter)


def download_file(url: str, save_path: Path, chunk_size: int = 128) -> None:
    """Download file from URL to specified path."""
    LOGGER.debug("Downloading File: '%s' to '%s'", url, save_path)
    r = requests.get(url, stream=True)
    with open(save_path, "wb") as fd:
        for chunk in r.iter_content(chunk_size=chunk_size):
            fd.write(chunk)


def file_or_folder_size_mb(path: Path) -> float:
    """Get size of file or all files in folder summed in MB."""

    def file_size_mb(file: Path) -> float:
        """Get size of file in MB."""
        return file.stat().st_size / (1024 * 1024)

    if path.is_file():
        return file_size_mb(path)
    return sum([file_size_mb(f) for f in path.rglob("*")])


def path_has_content(path: Path):
    """Check if path exists and is not an empty directory."""
    if path.is_dir():
        return bool(path.glob("*"))
    return path.exists() and path.stat().st_size


def typer_ask_overwrite_path(paths: Union[list[Path], Path]) -> bool:
    """Checks if the provided Paths do already exist.

    Ignores 0 size files and empty folders.
    Prints table of Paths with size and Changedate.
    Prompts user to continue or abort typer.

    Returns:
    ---------
    False, when we shall not overwrite files.
    True, when there are no files to override or we should override
    """
    if isinstance(paths, Path):
        paths = [paths]

    existing_paths = [p for p in paths if path_has_content(p)]
    if not existing_paths:
        return True
    table = Table()
    table.add_column("Name")
    table.add_column("Size", justify="right")
    table.add_column("Date Changed")

    for p in existing_paths:
        timestamp = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        path_size = round(file_or_folder_size_mb(p), 2)
        table.add_row(p.name, f"{path_size}mb", timestamp.strftime("%Y-%m-%d: %H:%M:%S"))
    LOGGER.warning("Found Existing Odoo Files:")
    Console().print(table)
    override = Confirm.ask("override?")
    if override:
        return True
    LOGGER.info("Aborting")
    return False


def sizeof_fmt(num: float, suffix: str = "B"):
    """Format number of bytes to human readable format.

    Args:
        num (int): Number of bytes.
        suffix (str, optional): Suffix to append. Defaults to "B".
    """
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Y{suffix}"
