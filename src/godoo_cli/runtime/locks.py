"""Runtime locks and incomplete-operation markers."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import threading
from collections.abc import Iterator
from configparser import ConfigParser
from contextlib import ExitStack, contextmanager
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def runtime_restore_marker(data_dir: Path, db_name: str) -> Path:
    """Return the marker for an unfinished database-and-filestore promotion."""
    return data_dir / ".godoo" / "pending-restores" / f"{hashlib.sha256(db_name.encode()).hexdigest()}.json"


def runtime_readiness_marker(data_dir: Path, db_name: str) -> Path:
    """Return the marker for incomplete runtime initialization."""
    return data_dir / ".godoo" / "pending-lifecycle" / f"{hashlib.sha256(db_name.encode()).hexdigest()}.json"


def read_runtime_lifecycle(marker: Path, db_name: str) -> dict[str, str] | None:
    """Read and validate a simple incomplete-init marker."""
    if not marker.exists():
        return None
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        message = f"Lifecycle marker {marker} is unreadable; restore or reset the runtime."
        raise RuntimeError(message) from error
    if not isinstance(value, dict) or set(value) != {"database", "outcome"} or value.get("database") != db_name:
        message = f"Runtime '{db_name}' has a legacy lifecycle marker; restore or reset the runtime before launching."
        raise RuntimeError(message)
    outcome = value.get("outcome")
    if not isinstance(outcome, str) or outcome not in {"ready", "bootstrapped", "restored"}:
        message = f"Lifecycle marker {marker} is invalid; restore or reset the runtime."
        raise RuntimeError(message)
    return {"database": db_name, "outcome": outcome}


def begin_runtime_lifecycle(data_dir: Path, db_name: str, *, outcome: str = "ready") -> Path:
    """Mark an initialization attempt incomplete before it mutates the runtime."""
    marker = runtime_readiness_marker(data_dir, db_name)
    write_runtime_lifecycle(marker, db_name, outcome=outcome)
    return marker


def write_runtime_lifecycle(marker: Path, db_name: str, *, outcome: str) -> None:
    """Atomically record the outcome whose hooks must run on a retry."""
    if outcome not in {"ready", "bootstrapped", "restored"}:
        message = f"Unknown lifecycle outcome: {outcome}"
        raise ValueError(message)
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"database": db_name, "outcome": outcome}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def finish_runtime_lifecycle(marker: Path) -> None:
    """Clear the initialization marker after all selected work succeeds."""
    marker.unlink(missing_ok=True)


def retire_runtime_lifecycle(data_dir: Path, db_name: str, *, owner: str | None = None) -> None:
    """Retire lifecycle work after an external replacement.

    ``owner='init'`` intentionally preserves the marker while seed replacement is in progress.

    Raises:
        ValueError: If the lifecycle owner is unknown.
    """
    if owner not in {None, "init"}:
        message = f"Unknown runtime lifecycle owner: {owner}"
        raise ValueError(message)
    if owner is None:
        finish_runtime_lifecycle(runtime_readiness_marker(data_dir, db_name))


def begin_runtime_restore(data_dir: Path, db_name: str, staged_database: str, *, replace_pending: bool = False) -> Path:
    """Record a pending promotion, replacing recovery state only when explicitly requested."""
    LOGGER.debug("Writing pending restore marker for %s", db_name)
    marker = runtime_restore_marker(data_dir, db_name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    destination = marker.with_suffix(".tmp") if replace_pending else marker
    try:
        with destination.open("w" if replace_pending else "x") as stream:
            json.dump({"database": db_name, "staged_database": staged_database}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if replace_pending:
            os.replace(destination, marker)
    finally:
        if replace_pending:
            destination.unlink(missing_ok=True)
    return marker


def runtime_data_directory(data_dir: Path | None, odoo_conf_path: Path | None = None) -> Path:
    """Resolve explicit data storage before Odoo configuration and its default."""
    if data_dir is not None:
        return data_dir.resolve()
    parser = ConfigParser(interpolation=None)
    if odoo_conf_path is not None:
        parser.read(odoo_conf_path)
    return Path(parser.get("options", "data_dir", fallback="/var/lib/odoo")).expanduser().resolve()


def database_inconsistency_reason(data_dir: Path, db_name: str, *, missing_or_empty: bool) -> str | None:
    """Return database and filestore recovery state that prevents readiness."""
    if runtime_restore_marker(data_dir, db_name).exists():
        return "an unfinished restore promotion"
    filestore = data_dir / "filestore" / db_name
    if (
        missing_or_empty
        and filestore.exists()
        and (not filestore.is_dir() or next(filestore.iterdir(), None) is not None)
    ):
        return "filestore data but no initialized database"
    return None


def runtime_inconsistency_reason(data_dir: Path, db_name: str, *, missing_or_empty: bool = False) -> str | None:
    """Describe an incomplete database/filestore pair or initialization attempt."""
    reason = database_inconsistency_reason(data_dir, db_name, missing_or_empty=missing_or_empty)
    if reason is not None:
        return reason
    marker = runtime_readiness_marker(data_dir, db_name)
    if not marker.exists():
        return None
    try:
        read_runtime_lifecycle(marker, db_name)
    except RuntimeError:
        return "unresolved legacy lifecycle marker"
    return "unfinished lifecycle work"


_HELD_LOCKS = threading.local()


@contextmanager
def runtime_locks(data_dir: Path | None, *db_names: str, odoo_conf_path: Path | None = None) -> Iterator[None]:
    """Hold stable per-database locks for one runtime operation."""
    directory = runtime_data_directory(data_dir, odoo_conf_path) / ".godoo" / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    held = getattr(_HELD_LOCKS, "paths", None)
    if held is None:
        held = _HELD_LOCKS.paths = set()
    paths = sorted({directory / f"{hashlib.sha256(name.encode()).hexdigest()}.lock" for name in db_names})
    with ExitStack() as stack:
        for path in paths:
            if path in held:
                continue
            stream = stack.enter_context(path.open("a"))
            LOGGER.debug("Waiting for runtime operation lock %s", path)
            fcntl.flock(stream, fcntl.LOCK_EX)
            held.add(path)
            stack.callback(held.remove, path)
        yield
