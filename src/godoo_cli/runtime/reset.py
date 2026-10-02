"""Odoo-managed database reset commands."""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from ..database.connection import DBConnection
from .locks import begin_runtime_restore, retire_runtime_lifecycle, runtime_data_directory, runtime_locks
from .odoo import run_odoo_command
from .promotion import (
    cleanup_database,
    complete_runtime_promotion,
    create_database,
    drop_database_strict,
    replace_filestore,
    rollback_database_swap,
    runtime_filestore_path,
    swap_database,
    temporary_database_name,
)

LOGGER = logging.getLogger(__name__)

CommandRunner = Callable[[Sequence[str]], int]


def _run_odoo_db(command: Sequence[str]) -> int:
    """Run an Odoo database command without invoking a shell."""
    LOGGER.info("Running Odoo database command: %s", " ".join(command))
    return run_odoo_command(command).returncode


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


def reset_runtime_from_template(
    *,
    db_name: str,
    db_template_name: str,
    odoo_bin_path: Path,
    odoo_conf_path: Path | None = None,
    data_dir: Path | None = None,
    runner: CommandRunner = _run_odoo_db,
    odoo_version: int | None = None,
    connection: DBConnection | None = None,
    **_: object,
) -> int:
    """Replace a database and filestore from a template through staging."""
    del odoo_bin_path, runner, odoo_version
    with runtime_locks(data_dir, db_name, db_template_name, odoo_conf_path=odoo_conf_path):
        if db_name == db_template_name:
            LOGGER.error("Template and target database names must differ.")
            return 2
        runtime_data = runtime_data_directory(data_dir, odoo_conf_path)
        target_connection = connection or DBConnection.from_odoo_config(db_name, odoo_conf_path)
        target = runtime_filestore_path(runtime_data, db_name)
        source = runtime_filestore_path(runtime_data, db_template_name)
        stage = target.with_name(f".{target.name}.reset-{uuid.uuid4().hex}")
        staged_database = temporary_database_name("reset")
        created = False
        marker = None
        try:
            if source.is_dir():
                shutil.copytree(source, stage)
            else:
                stage.mkdir(parents=True)
            create_database(target_connection.with_db(staged_database), db_template_name)
            created = True
            marker = begin_runtime_restore(runtime_data, db_name, staged_database)
            backup = swap_database(target_connection, staged_database)
            created = False
            complete_runtime_promotion(
                connection=target_connection,
                marker=marker,
                backup_database=backup,
                staged_filestore=stage,
                target_filestore=target,
                database_cleaner=cleanup_database,
                database_rollback=rollback_database_swap,
                filestore_replacer=replace_filestore,
            )
            return 0
        finally:
            if created:
                cleanup_database(target_connection.with_db(staged_database))
            if stage.exists() and (marker is None or not marker.exists()):
                shutil.rmtree(stage, ignore_errors=True)
            elif stage.exists():
                LOGGER.warning("Retaining reset staging directory %s because promotion remains pending", stage)


def reset_empty_runtime(
    *,
    db_name: str,
    odoo_bin_path: Path,
    odoo_conf_path: Path | None = None,
    data_dir: Path | None = None,
    runner: CommandRunner = _run_odoo_db,
    odoo_version: int | None = None,
    connection: DBConnection | None = None,
    database_cleaner: Callable[[DBConnection], None] | None = None,
    **_: object,
) -> int:
    """Drop a database and its filestore as one recoverable operation."""
    del odoo_bin_path, runner, odoo_version
    # Move the filestore aside before dropping the database so failure can restore the prior pair.
    with runtime_locks(data_dir, db_name, odoo_conf_path=odoo_conf_path):
        target_connection = connection or DBConnection.from_odoo_config(db_name, odoo_conf_path)
        runtime_data = runtime_data_directory(data_dir, odoo_conf_path)
        filestore = runtime_filestore_path(runtime_data, db_name)
        backup = filestore.with_name(f".{db_name}.before-drop-{uuid.uuid4().hex}") if filestore else None
        marker = None
        try:
            marker = begin_runtime_restore(runtime_data, db_name, "drop")
            if filestore is not None and filestore.exists():
                assert backup is not None
                os.replace(filestore, backup)
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
            shutil.rmtree(backup)
        retire_runtime_lifecycle(runtime_data, db_name)
        if marker is not None:
            marker.unlink(missing_ok=True)
        return 0
