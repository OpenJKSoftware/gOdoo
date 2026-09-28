"""Provide process, logging, file, and terminal helpers."""

import atexit
import contextlib
import logging
import os
import re
import sys
import threading

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.traceback import install as install_rich_traceback

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
        """Return whether a captured line should be forwarded."""
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


def set_logging(verbose: bool = False, log_filter: str | None = None) -> None:
    """Set the logging configuration according to passed arguments.

    Args:
        verbose: Enable DEBUG-level logging with rich tracebacks.
        log_filter: Optional regex pattern; only log records whose logger name
            matches the pattern will be emitted.
    """
    if verbose:
        install_rich_traceback(suppress=[click])
        logging.basicConfig(
            level=logging.DEBUG,
            format="[italic bright_black]{name}:[/] {message}",
            style="{",
            handlers=[
                RichHandler(
                    console=Console(stderr=True),
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
            handlers=[
                RichHandler(console=Console(stderr=True), level=logging.INFO, show_path=False, rich_tracebacks=False)
            ],
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    if log_filter:
        pattern = re.compile(log_filter)
        regex_filter = RegexLogFilter(log_filter)
        for handler in logging.root.handlers:
            handler.addFilter(regex_filter)
        _install_fd_filter(pattern)
        LOGGER.debug("Log filter active: '%s'", log_filter)
