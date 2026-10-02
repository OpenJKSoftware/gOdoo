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


def retire_runtime_lifecycle(
    data_dir: Path,
    db_name: str,
    *,
    owner: str | None = None,
) -> None:
    """Retire stale lifecycle work after an external database replacement."""
    if owner not in {None, "init", "prepare"}:
        message = f"Unknown runtime lifecycle owner: {owner}"
        raise ValueError(message)
    if owner is None:
        runtime_readiness_marker(data_dir, db_name).unlink(missing_ok=True)


def runtime_preparation_identity(
    *,
    strategy: str,
    source_db: str,
    archive_path: Path | None,
    filestore_path: Path | None,
    original_filestore: Path | None,
    target: dict[str, object] | None = None,
) -> dict[str, object]:
    """Bind preparation to the selected paths and their current stat identity."""

    def identity(path: Path | None) -> dict[str, object] | None:
        if path is None:
            return None
        resolved = path.expanduser().resolve()
        stat = resolved.stat()
        return {
            "path": str(resolved),
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
        }

    return {
        "strategy": strategy,
        "source_db": source_db,
        "archive": identity(archive_path),
        "filestore": identity(filestore_path),
        "original_filestore": identity(original_filestore),
        "target": target or {},
    }


def read_runtime_lifecycle(marker: Path, db_name: str) -> dict[str, object] | None:
    """Read lifecycle state, accepting only recognized legacy or bound markers."""
    if not marker.exists():
        return None
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        message = f"Lifecycle marker {marker} unreadable; resolve recovery state manually."
        raise RuntimeError(message) from error

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
    valid_legacy = (
        isinstance(state, dict) and required <= state.keys() and state["schema_version"] == 1 and "plan" not in state
    )
    valid_bound = (
        isinstance(state, dict)
        and required | {"plan"} <= state.keys()
        and state["schema_version"] == 2
        and isinstance(state["plan"], dict)
        and isinstance(state["plan"].get("identity"), dict)
        and isinstance(state["plan"].get("diagnostics"), dict)
    )
    valid_prepare_handoff = (
        isinstance(state, dict)
        and required | {"owner", "preparation", "preparation_complete"} <= state.keys()
        and state["schema_version"] == 3
        and state["owner"] == "prepare"
        and isinstance(state["preparation"], dict)
        and isinstance(state["preparation_complete"], bool)
    )
    if (
        not isinstance(state, dict)
        or not (valid_legacy or valid_bound or valid_prepare_handoff)
        or state["database"] != db_name
        or not isinstance(state["outcome"], str)
        or not isinstance(state["pending_phase"], str)
        or (state["outcome"], state["pending_phase"]) not in valid_transitions
    ):
        message = f"Lifecycle marker {marker} is invalid; resolve recovery state manually."
        raise RuntimeError(message)
    return state


# Keep owner-aware marker transitions together under the database lock.
def begin_runtime_lifecycle(  # noqa: C901
    data_dir: Path,
    db_name: str,
    *,
    plan: dict[str, object] | None = None,
    adopt_legacy_plan: bool = False,
    owner: str = "init",
    preparation: dict[str, object] | None = None,
) -> Path:
    """Create or validate a lock-owned lifecycle marker."""
    marker = runtime_readiness_marker(data_dir, db_name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    state = read_runtime_lifecycle(marker, db_name)
    if owner == "prepare":
        if preparation is None:
            message = "Preparation handoff requires bound preparation inputs."
            raise ValueError(message)
        if state is not None:
            if state["schema_version"] != 3 or state["preparation"] != preparation:
                message = "Pending preparation inputs differ; resolve preparation recovery first."
                raise RuntimeError(message)
            return marker
        write_runtime_lifecycle(
            marker,
            db_name,
            outcome="unknown",
            pending_phase="initialize",
            owner="prepare",
            preparation=preparation,
            preparation_complete=False,
        )
        return marker
    if state is None:
        if adopt_legacy_plan:
            message = "--adopt-pending-plan requires unbound legacy lifecycle marker."
            raise RuntimeError(message)
        write_runtime_lifecycle(marker, db_name, outcome="unknown", pending_phase="initialize", plan=plan)
        return marker
    if adopt_legacy_plan and state["schema_version"] != 1:
        message = "--adopt-pending-plan only applies to unbound schema-one lifecycle markers."
        raise RuntimeError(message)
    if state["schema_version"] == 3:
        assert isinstance(state["preparation"], dict)
        if not state["preparation_complete"]:
            message = "Pending preparation is incomplete; recover with godoo db prepare first."
            raise RuntimeError(message)
        if plan is None or not isinstance(plan.get("identity"), dict):
            message = "A completed preparation handoff requires a bound runtime init plan."
            raise RuntimeError(message)
        plan_identity = plan["identity"]
        assert isinstance(plan_identity, dict)
        target_identity = state["preparation"].get("target")
        database_identity = plan_identity.get("database")
        runtime_identity = plan_identity.get("runtime")
        if not isinstance(database_identity, dict) or not isinstance(runtime_identity, dict):
            message = "Runtime init plan is missing its target identity."
            raise RuntimeError(message)
        connection_identity = database_identity.get("connection")
        if not isinstance(connection_identity, dict):
            message = "Runtime init plan is missing its database connection identity."
            raise RuntimeError(message)
        expected_target = {
            "runtime_path": runtime_identity.get("install_path"),
            "database": database_identity.get("name"),
            "data_dir": database_identity.get("data_dir"),
            "connection": connection_identity,
        }
        if target_identity and target_identity != expected_target:
            message = "Prepared target differs from runtime init; resolve recovery state."
            raise RuntimeError(message)
        write_runtime_lifecycle(
            marker,
            db_name,
            outcome=str(state["outcome"]),
            pending_phase=str(state["pending_phase"]),
            plan=plan,
            preparation_provenance=state["preparation"],
        )
        return marker
    if state["schema_version"] == 1:
        if plan is None and not adopt_legacy_plan:
            return marker
        if not adopt_legacy_plan:
            message = (
                "Pending lifecycle marker has no bound plan; verify recovery inputs and "
                "rerun with --adopt-pending-plan to bind them."
            )
            raise RuntimeError(message)
        write_runtime_lifecycle(
            marker,
            db_name,
            outcome=str(state["outcome"]),
            pending_phase=str(state["pending_phase"]),
            plan=plan,
        )
        return marker
    if plan is None:
        message = "A bound pending lifecycle plan must be resumed through runtime init."
        raise RuntimeError(message)
    if adopt_legacy_plan:
        message = "--adopt-pending-plan only applies to unbound legacy lifecycle markers."
        raise RuntimeError(message)
    stored_plan = state["plan"]
    assert isinstance(stored_plan, dict)
    if stored_plan["identity"] != plan["identity"]:
        message = (
            "Pending lifecycle plan differs from requested initialization; resolve pending "
            "lifecycle state before changing selected inputs."
        )
        raise RuntimeError(message)
    if stored_plan != plan:
        write_runtime_lifecycle(
            marker,
            db_name,
            outcome=str(state["outcome"]),
            pending_phase=str(state["pending_phase"]),
            plan=plan,
        )
    return marker


def write_runtime_lifecycle(
    marker: Path,
    db_name: str,
    *,
    outcome: str,
    pending_phase: str,
    plan: dict[str, object] | None = None,
    owner: str | None = None,
    preparation: dict[str, object] | None = None,
    preparation_complete: bool = True,
    preparation_provenance: dict[str, object] | None = None,
) -> None:
    """Atomically persist an optional plan and the next lifecycle phase."""
    previous_state = read_runtime_lifecycle(marker, db_name) if marker.exists() else None
    if plan is None and previous_state is not None and previous_state["schema_version"] == 2:
        message = "A bound pending lifecycle plan cannot be replaced by an unbound marker."
        raise RuntimeError(message)
    if preparation_provenance is None and previous_state is not None:
        retained = previous_state.get("preparation_provenance")
        if isinstance(retained, dict):
            preparation_provenance = retained
    marker_value: dict[str, object] = {
        "schema_version": 3 if owner == "prepare" else 2 if plan is not None else 1,
        "database": db_name,
        "outcome": outcome,
        "pending_phase": pending_phase,
    }
    if plan is not None:
        marker_value["plan"] = plan
    if owner == "prepare":
        if preparation is None:
            message = "Preparation handoff requires bound preparation inputs."
            raise ValueError(message)
        marker_value.update(
            owner="prepare",
            preparation=preparation,
            preparation_complete=preparation_complete,
        )
    elif preparation_provenance is not None:
        marker_value["preparation_provenance"] = preparation_provenance
    temporary = marker.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(marker_value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def finish_runtime_lifecycle(marker: Path) -> None:
    """Clear lifecycle marker only after final phase succeeds."""
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
