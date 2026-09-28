"""Coordinate operations on runtimes sharing a filestore volume."""

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
_HELD_LOCKS = threading.local()


def runtime_restore_marker(data_dir: Path, db_name: str) -> Path:
    """Return the marker identifying an unfinished database-and-filestore promotion."""
    return data_dir / ".godoo" / "pending-restores" / f"{hashlib.sha256(db_name.encode()).hexdigest()}.json"


def runtime_readiness_marker(data_dir: Path, db_name: str) -> Path:
    """Return the lock-owned lifecycle marker for a runtime."""
    return data_dir / ".godoo" / "pending-lifecycle" / f"{hashlib.sha256(db_name.encode()).hexdigest()}.json"


def read_runtime_lifecycle(marker: Path, db_name: str) -> dict[str, str] | None:
    """Read a lifecycle marker, refusing legacy or corrupt recovery state."""
    if not marker.exists():
        return None
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        msg = f"Lifecycle marker {marker} is unreadable; resolve recovery state manually."
        raise RuntimeError(msg) from error
    required = {"schema_version", "database", "outcome", "pending_phase"}
    valid_transitions = {
        ("unknown", "initialize"),
        ("bootstrapped", "initialize"),
        ("restored", "initialize"),
        ("ready", "after-bootstrap"),
        ("bootstrapped", "after-bootstrap"),
        ("restored", "after-restore"),
        ("ready", "reconcile"),
        ("bootstrapped", "reconcile"),
        ("restored", "reconcile"),
        ("ready", "after-reconcile"),
        ("bootstrapped", "after-reconcile"),
        ("restored", "after-reconcile"),
    }
    if (
        not isinstance(state, dict)
        or not required <= state.keys()
        or state["schema_version"] != 1
        or state["database"] != db_name
        or not isinstance(state["outcome"], str)
        or not isinstance(state["pending_phase"], str)
        or (state["outcome"], state["pending_phase"]) not in valid_transitions
    ):
        msg = f"Lifecycle marker {marker} is legacy or incompatible; resolve recovery state manually."
        raise RuntimeError(msg)
    return state


def begin_runtime_lifecycle(data_dir: Path, db_name: str) -> Path:
    """Create or validate a versioned lifecycle marker while holding its runtime lock."""
    marker = runtime_readiness_marker(data_dir, db_name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker.exists():
        read_runtime_lifecycle(marker, db_name)
        return marker
    write_runtime_lifecycle(marker, db_name, outcome="unknown", pending_phase="initialize")
    return marker


def write_runtime_lifecycle(marker: Path, db_name: str, *, outcome: str, pending_phase: str) -> None:
    """Atomically persist the next lifecycle phase before it runs."""
    temporary = marker.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(
                {"schema_version": 1, "database": db_name, "outcome": outcome, "pending_phase": pending_phase}, stream
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def finish_runtime_lifecycle(marker: Path) -> None:
    """Clear a lifecycle marker only after its final phase succeeds."""
    marker.unlink(missing_ok=True)


def begin_runtime_restore(data_dir: Path, db_name: str, staged_database: str) -> Path:
    """Record a pending promotion, refusing to overwrite an unresolved restore."""
    marker = runtime_restore_marker(data_dir, db_name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    with marker.open("x") as stream:
        json.dump({"database": db_name, "staged_database": staged_database}, stream)
        stream.flush()
        os.fsync(stream.fileno())
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


def runtime_inconsistency_reason(data_dir: Path, db_name: str, *, missing_or_empty: bool) -> str | None:
    """Return database recovery state or unfinished lifecycle work."""
    reason = database_inconsistency_reason(data_dir, db_name, missing_or_empty=missing_or_empty)
    if reason is not None:
        return reason
    marker = runtime_readiness_marker(data_dir, db_name)
    if marker.exists():
        try:
            read_runtime_lifecycle(marker, db_name)
        except RuntimeError:
            return "unresolved legacy lifecycle marker"
        return "unfinished lifecycle work"
    return None


@contextmanager
def runtime_locks(data_dir: Path | None, *db_names: str, odoo_conf_path: Path | None = None) -> Iterator[None]:
    """Hold ordered, reentrant file locks until the operation finishes.

    Every container managing a runtime must share its data volume. Lock files
    stay in place after release so waiting processes always use the same inode.

    Yields:
        Control while all requested database-and-filestore pairs are locked.
    """
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
