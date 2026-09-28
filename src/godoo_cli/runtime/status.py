"""Read-only runtime status inspection."""

import json
import logging
from pathlib import Path
from typing import Any

import psycopg2

from ..__about__ import __version__
from ..database.connection import DBConnection
from ..database.state import BOOTSTRAP_EXIT_CODE, DbBootstrapStatus, classify_bootstrap_state
from .locks import runtime_inconsistency_reason

LOGGER = logging.getLogger(__name__)

_STATE_NAMES = {
    DbBootstrapStatus.BOOTSTRAPPED: "ready",
    DbBootstrapStatus.NO_DB: "missing",
    DbBootstrapStatus.EMPTY_DB: "empty",
    DbBootstrapStatus.INVALID_DB: "inconsistent",
}


def release_metadata(provenance_path: Path) -> dict[str, Any]:
    """Read production release identifiers when provenance is available."""
    if not provenance_path.is_file():
        return {"available": False, "reason": "No production provenance was found."}
    try:
        metadata = json.loads(provenance_path.read_text())
        sources = [
            {
                key: source[key]
                for key in ("role", "branch", "base_commit", "resolved_commit", "merge_commits")
                if key in source
            }
            for source in metadata.get("sources", [])
        ]
        return {
            "available": True,
            "kind": "production",
            "sources": sources,
            "build_info": metadata.get("build_info", {}),
            "image": metadata.get("image", {}),
            "project_content_sha256": metadata.get("project_content_sha256"),
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return {"available": False, "reason": "Runtime provenance could not be read; rebuild or synchronize metadata."}


def inspect_runtime(connection: DBConnection, data_dir: Path, provenance_path: Path) -> tuple[dict[str, Any], int]:
    """Return database state, recovery advice, and release identifiers without writes."""
    payload: dict[str, Any] = {
        "schema_version": 1,
        "database": connection.db_name,
        "godoo_version": __version__,
        "release": release_metadata(provenance_path),
    }
    try:
        status = classify_bootstrap_state(connection)
        state, exit_code = _STATE_NAMES[status], BOOTSTRAP_EXIT_CODE[status]
        if runtime_inconsistency_reason(
            data_dir,
            connection.db_name,
            missing_or_empty=status in (DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB),
        ):
            state, exit_code = "inconsistent", 22
        reasons = {
            "ready": "The database has an initialized Odoo base module.",
            "missing": "The database is missing; run runtime init to restore a seed or bootstrap.",
            "empty": "The database is empty; run runtime init to restore a seed or bootstrap.",
            "inconsistent": "Recover an unfinished restore or rerun runtime init to finish lifecycle work.",
        }
        payload.update(state=state, reason=reasons[state])
    except (psycopg2.Error, OSError):
        payload.update(state="unavailable", reason="Check PostgreSQL connectivity, credentials, and filestore access.")
        exit_code = 1
    payload["exit_code"] = exit_code
    return payload, exit_code
