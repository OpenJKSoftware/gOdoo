"""Report progress during finite batch operations."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Literal

LOGGER = logging.getLogger(__name__)
WAITING_INTERVAL_SECONDS = 10


@dataclass
class ProgressPhase:
    """Report elapsed time without capturing subprocess output."""

    name: str
    heartbeat_seconds: float = WAITING_INTERVAL_SECONDS
    heartbeat: bool = True
    failed: bool = False
    logger: logging.Logger = LOGGER
    _started: float = field(init=False, repr=False)
    _stop: threading.Event = field(init=False, repr=False)
    _thread: threading.Thread = field(init=False, repr=False)

    def __enter__(self) -> ProgressPhase:
        """Start the elapsed-time reporter."""
        self._started = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.logger.info("%s started", self.name)
        self._thread.start()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        """Stop reporting and record whether the operation completed."""
        self._stop.set()
        self._thread.join()
        elapsed = time.monotonic() - self._started
        if exception_type is not None or self.failed:
            self.logger.info("%s failed after %.1fs", self.name, elapsed)
        else:
            self.logger.info("%s completed in %.1fs", self.name, elapsed)
        return False

    def _heartbeat(self) -> None:
        if not self.heartbeat:
            return
        while not self._stop.wait(self.heartbeat_seconds):
            self.logger.info("%s still running after %.1fs", self.name, time.monotonic() - self._started)
