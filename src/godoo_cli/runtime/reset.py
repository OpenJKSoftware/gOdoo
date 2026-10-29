"""Odoo-managed database reset commands."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from ..database.connection import DBConnection
from ..database.state import database_exists
from .baseline import baseline_database_name
from .locks import (
    begin_runtime_lifecycle,
    begin_runtime_restore,
    retire_runtime_lifecycle,
    runtime_data_directory,
    runtime_locks,
    runtime_restore_marker,
)
from .promotion import (
    cleanup_database,
    create_database,
    drop_database_strict,
    runtime_filestore_path,
    swap_database,
)

LOGGER = logging.getLogger(__name__)

CommandRunner = Callable[[Sequence[str]], int]


def _run_rsync(command: Sequence[str]) -> int:
    """Synchronize reset filestore content without invoking Odoo."""
    return subprocess.run(command, check=False).returncode


def odoo_db_command(
    *,
    odoo_bin_path: Path,
    odoo_conf_path: Path | None,
    data_dir: Path | None,
    arguments: Sequence[str],
    odoo_version: int | None = None,
) -> list[str]:
    """Build a database command, using PostgreSQL tools before Odoo 19."""
    if odoo_version is not None and odoo_version < 19:
        if not arguments:
            return []
        operation = arguments[0]
        if operation == "drop":
            return ["dropdb", *arguments[1:]]
        if operation == "duplicate":
            source = arguments[-2]
            target = arguments[-1]
            return ["createdb", f"--template={source}", target]
        message = "Unsupported pre-Odoo-19 database operation: " + " ".join(arguments)
        raise ValueError(message)
    command = [str(odoo_bin_path), "db"]
    if odoo_conf_path is not None:
        command.extend(["--config", str(odoo_conf_path)])
    if data_dir is not None:
        command.extend(["--data-dir", str(data_dir)])
    command.extend(arguments)
    return command


def _validate_reset_retry(marker: Path, db_name: str, staged_database: str) -> None:
    """Require an existing restore marker to belong to this reset."""
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        message = f"Reset for '{db_name}' has an unreadable pending restore marker."
        raise RuntimeError(message) from error
    if not isinstance(state, dict) or (state.get("database"), state.get("staged_database")) != (
        db_name,
        staged_database,
    ):
        message = f"Reset for '{db_name}' has an unrelated pending restore marker."
        raise RuntimeError(message)


def reset_runtime_from_template(
    *,
    connection: DBConnection,
    data_dir: Path,
    template: str = "",
    odoo_conf_path: Path | None = None,
    runner: CommandRunner = _run_rsync,
    database_exists_getter: Callable[[DBConnection, str], bool] = database_exists,
) -> int:
    """Reset a runtime from a saved baseline or an explicit database template."""
    target_connection = connection
    db_name = target_connection.db_name
    runtime_data = runtime_data_directory(data_dir, odoo_conf_path)
    template = template or baseline_database_name(db_name)
    if template == db_name:
        LOGGER.error("Template and target database names must differ.")
        return 2
    target_filestore = runtime_filestore_path(runtime_data, db_name)
    source = runtime_filestore_path(runtime_data, template)
    staged_database = f"godoo_reset_{hashlib.sha256(db_name.encode()).hexdigest()[:32]}"

    LOGGER.info("Resetting runtime '%s' from baseline '%s'; waiting for runtime locks", db_name, template)
    with runtime_locks(runtime_data, db_name, template):
        LOGGER.info("Checking reset baseline '%s' and filestore %s", template, source)
        baseline_marker = runtime_restore_marker(runtime_data, template)
        if baseline_marker.exists():
            message = (
                f"Reset baseline '{template}' has an unfinished promotion. "
                "Restore a fresh runtime with --keep-reset-baseline before resetting."
            )
            raise RuntimeError(message)
        if not database_exists_getter(target_connection.with_db("postgres"), template):
            message = (
                f"Reset baseline database '{template}' is missing. "
                "Restore a fresh runtime with --keep-reset-baseline before resetting."
            )
            raise RuntimeError(message)
        if not source.is_dir():
            message = (
                f"Reset baseline filestore '{source}' is missing. "
                "Restore a fresh runtime with --keep-reset-baseline before resetting."
            )
            raise RuntimeError(message)

        marker = runtime_restore_marker(runtime_data, db_name)
        if marker.exists():
            _validate_reset_retry(marker, db_name, staged_database)

        LOGGER.info("Copying baseline database '%s' to staging database '%s'", template, staged_database)
        create_database(target_connection.with_db(staged_database), template, prefer_cow=True)
        if not marker.exists():
            marker = begin_runtime_restore(runtime_data, db_name, staged_database)

        target_filestore.mkdir(parents=True, exist_ok=True)
        command = ["rsync", "-a", "--delete", f"{source}/", f"{target_filestore}/"]
        LOGGER.info("Synchronizing filestore from %s to %s with rsync --delete", source, target_filestore)
        try:
            result = runner(command)
        except OSError as error:
            message = (
                f"Could not reset filestore from baseline '{template}': {error}. "
                "The pending restore marker was retained; run reset again to retry."
            )
            raise RuntimeError(message) from error
        if result:
            message = (
                f"Could not reset filestore from baseline '{template}'. "
                "The pending restore marker was retained; run reset again to retry."
            )
            raise RuntimeError(message)

        LOGGER.info("Replacing runtime database '%s' with staging database '%s'", db_name, staged_database)
        backup = swap_database(target_connection, staged_database)
        if backup is not None:
            LOGGER.info("Removing previous database '%s'", backup)
            cleanup_database(target_connection.with_db(backup))
        LOGGER.info("Marking runtime '%s' for initialization after reset", db_name)
        retire_runtime_lifecycle(runtime_data, db_name)
        begin_runtime_lifecycle(runtime_data, db_name, outcome="restored")
        marker.unlink()
        LOGGER.info("Reset of runtime '%s' from baseline '%s' completed; initialization is required", db_name, template)
        return 0


def reset_empty_runtime(
    *,
    connection: DBConnection,
    data_dir: Path,
    odoo_conf_path: Path | None = None,
    database_cleaner: Callable[[DBConnection], None] | None = None,
) -> int:
    """Drop a database and its filestore as one recoverable operation."""
    target_connection = connection
    db_name = target_connection.db_name
    # Move the filestore aside before dropping the database so failure can restore the prior pair.
    LOGGER.info("Emptying runtime '%s'; waiting for runtime lock", db_name)
    with runtime_locks(data_dir, db_name, odoo_conf_path=odoo_conf_path):
        runtime_data = runtime_data_directory(data_dir, odoo_conf_path)
        filestore = runtime_filestore_path(runtime_data, db_name)
        backup = filestore.with_name(f".{db_name}.before-drop-{uuid.uuid4().hex}") if filestore else None
        marker = None
        try:
            marker = begin_runtime_restore(runtime_data, db_name, "drop")
            if filestore is not None and filestore.exists():
                assert backup is not None
                LOGGER.info("Moving filestore %s aside to %s before database drop", filestore, backup)
                os.replace(filestore, backup)
            LOGGER.info("Dropping runtime database '%s'", db_name)
            (database_cleaner or drop_database_strict)(target_connection)
        except Exception:
            LOGGER.exception("Could not drop runtime database '%s'; restoring filestore", db_name)
            recovered = True
            if backup is not None and backup.exists():
                if filestore.exists():
                    recovered = False
                    LOGGER.warning("Cannot determine which filestore is current for runtime '%s'", db_name)
                else:
                    try:
                        os.replace(backup, filestore)
                    except OSError:
                        recovered = False
                        LOGGER.exception("Could not restore filestore for runtime '%s'", db_name)
            if marker is not None and recovered:
                marker.unlink(missing_ok=True)
            return 1
        if backup is not None and backup.exists():
            LOGGER.info("Removing dropped runtime filestore %s", backup)
            shutil.rmtree(backup)
        LOGGER.info("Clearing lifecycle state for runtime '%s'", db_name)
        retire_runtime_lifecycle(runtime_data, db_name)
        if marker is not None:
            marker.unlink(missing_ok=True)
        LOGGER.info("Empty reset of runtime '%s' completed", db_name)
        return 0
