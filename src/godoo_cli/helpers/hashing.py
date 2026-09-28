"""Stable SHA-256 helpers shared by workspace state."""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    """Hash a file without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(value: Any, length: int = 64) -> str:
    """Hash a JSON-compatible value with stable ordering."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:length]
