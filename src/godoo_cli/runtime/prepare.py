"""Select and execute a safe database preparation strategy."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, NoReturn

import psycopg2

from ..models import GodooConfig
from .archive import load_runtime_archive, validate_native_runtime_archive
from .cow import duplicate_cow_runtime
from .lifecycle import ensure_runtime
from .locks import begin_runtime_lifecycle, finish_runtime_lifecycle, runtime_locks, write_runtime_lifecycle
from .odoo import require_supported_odoo_runtime
from .restore import restore_custom_runtime, validate_custom_dump, validate_filestore

LOGGER = logging.getLogger(__name__)


class PrepareStrategy(StrEnum):
    """Supported ways to create or replace a runtime."""

    AUTO = "auto"
    COW = "cow"
    POSTGRES = "postgres"
    ODOO = "odoo"
    BOOTSTRAP = "bootstrap"


@dataclass(frozen=True)
class PreparePlan:
    """A selected strategy and the reason it was selected."""

    strategy: PrepareStrategy
    reason: str


def select_prepare_strategy(
    strategy: str | PrepareStrategy = PrepareStrategy.AUTO,
    *,
    cow_available: bool = False,
    postgres_archive: bool = False,
    odoo_archive: bool = False,
) -> PreparePlan:
    """Choose a strategy without touching the target database or filestore.

    Automatic selection follows the documented speed order. Forced strategies
    fail before mutation when their prerequisites are not available.

    Returns:
        The selected preparation strategy and its reason.

    Raises:
        ValueError: If the strategy is unknown or its prerequisites are absent.
    """
    try:
        requested = PrepareStrategy(strategy)
    except ValueError as error:
        message = f"Unknown database preparation strategy: {strategy}"
        raise ValueError(message) from error
    if requested == PrepareStrategy.AUTO:
        if cow_available:
            return PreparePlan(PrepareStrategy.COW, "copy-on-write clone is available")
        if postgres_archive:
            return PreparePlan(PrepareStrategy.POSTGRES, "a PostgreSQL archive was supplied")
        if odoo_archive:
            return PreparePlan(PrepareStrategy.ODOO, "an Odoo archive was supplied")
        return PreparePlan(PrepareStrategy.BOOTSTRAP, "no reusable runtime source was supplied")
    if requested == PrepareStrategy.COW and not cow_available:
        message = "The forced CoW strategy is unavailable; check PostgreSQL and filestore capabilities."
        raise ValueError(message)
    if requested == PrepareStrategy.POSTGRES and not postgres_archive:
        message = "The forced PostgreSQL strategy requires a PostgreSQL archive."
        raise ValueError(message)
    if requested == PrepareStrategy.ODOO and not odoo_archive:
        message = "The forced Odoo strategy requires an Odoo archive."
        raise ValueError(message)
    return PreparePlan(requested, "strategy was forced by the caller")


PrepareCallback = Callable[..., Any]


def _missing_prepare_argument(message: str) -> NoReturn:
    """Raise a consistent error for incomplete preparation strategies."""
    raise ValueError(message)


def _cow_capability_available(config: GodooConfig, source_db: str) -> bool:
    """Check CoW prerequisites without changing PostgreSQL or filestore state."""
    source_filestore = config.data_dir / "filestore" / source_db
    if not source_filestore.is_dir():
        return False
    try:
        with config.db_connection.with_db("postgres", readonly=True).connect() as cursor:
            cursor.execute("SHOW server_version_num")
            version_row = cursor.fetchone()
            if not version_row:
                return False
            version = int(version_row[0])
            cursor.execute("SHOW file_copy_method")
            method_row = cursor.fetchone()
            if not method_row:
                return False
            method = str(method_row[0])
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)", [source_db])
            exists_row = cursor.fetchone()
            if not exists_row:
                return False
            exists = bool(exists_row[0])
    except (OSError, psycopg2.Error, TypeError, ValueError):
        LOGGER.debug("CoW capability preflight failed", exc_info=True)
        return False
    return version >= 180000 and method == "clone" and exists


def prepare_runtime_pair(
    *,
    data_dir: Path,
    db_name: str,
    strategy: str | PrepareStrategy = PrepareStrategy.AUTO,
    cow_available: bool = False,
    postgres_archive: bool = False,
    odoo_archive: bool = False,
    restore: PrepareCallback | None = None,
    clone: PrepareCallback | None = None,
    bootstrap: PrepareCallback | None = None,
    completion: PrepareCallback | None = None,
    source_db: str | None = None,
    **kwargs: Any,
) -> PreparePlan:
    """Select a plan, mark lifecycle work, and run its one mutation callback.

    Callbacks are deliberately supplied by the execution layer. This keeps
    database strategy selection independent from Odoo release specific APIs and
    makes the same protocol usable by host and container callers. Restore and
    clone callbacks own their detailed backup and rollback operations.

    Returns:
        The selected preparation strategy and its reason.

    Raises:
        RuntimeError: If a lifecycle callback fails.
        ValueError: If no callback is configured for the selected strategy.
    """
    plan = select_prepare_strategy(
        strategy,
        cow_available=cow_available,
        postgres_archive=postgres_archive,
        odoo_archive=odoo_archive,
    )
    callback = {
        PrepareStrategy.COW: clone,
        PrepareStrategy.POSTGRES: restore,
        PrepareStrategy.ODOO: restore,
        PrepareStrategy.BOOTSTRAP: bootstrap,
    }[plan.strategy]
    if callback is None:
        message = f"No callback is configured for database strategy '{plan.strategy.value}'."
        raise ValueError(message)
    if plan.strategy is PrepareStrategy.COW and not source_db:
        msg = "The CoW strategy requires a source database for lock ownership."
        raise ValueError(msg)
    lock_names = (db_name, source_db) if source_db is not None else (db_name,)
    with runtime_locks(data_dir, *lock_names):
        marker = begin_runtime_lifecycle(data_dir, db_name)
        try:
            result = callback(**kwargs)
        except BaseException:
            LOGGER.exception("Database preparation failed using %s", plan.strategy.value)
            raise
        if isinstance(result, int) and not isinstance(result, bool) and result != 0:
            msg = f"Database preparation failed using {plan.strategy.value} (exit code {result})."
            raise RuntimeError(msg)
        if plan.strategy is PrepareStrategy.BOOTSTRAP and result is not True:
            msg = "Bootstrap preparation did not create a runtime."
            raise RuntimeError(msg)
        outcome = "bootstrapped" if plan.strategy is PrepareStrategy.BOOTSTRAP else "restored"
        pending_phase = "after-bootstrap" if outcome == "bootstrapped" else "after-restore"
        write_runtime_lifecycle(marker, db_name, outcome=outcome, pending_phase=pending_phase)
        if completion is None:
            return plan
        completion_result = completion()
        if isinstance(completion_result, int) and completion_result != 0:
            msg = f"Database preparation completion failed (exit code {completion_result})."
            raise RuntimeError(msg)
        finish_runtime_lifecycle(marker)
        return plan


def prepare_runtime(
    config: GodooConfig,
    *,
    strategy: str,
    source_db: str = "",
    archive_path: Path | None = None,
    filestore_path: Path | None = None,
    force: bool = False,
) -> PreparePlan:
    """Prepare a runtime pair through the single domain-owned strategy contract."""
    # Classify and validate inputs before selecting a strategy that may mutate the runtime pair.
    postgres = archive_path is not None and archive_path.suffix.lower() not in {".zip", ".odoo"}
    odoo = archive_path is not None and not postgres
    cow = _cow_capability_available(config, source_db) if source_db else False
    plan = select_prepare_strategy(strategy, cow_available=cow, postgres_archive=postgres, odoo_archive=odoo)
    if plan.strategy is not PrepareStrategy.POSTGRES:
        require_supported_odoo_runtime(config.odoo_install_folder)
    if plan.strategy is PrepareStrategy.POSTGRES:
        if archive_path is None or filestore_path is None:
            msg = "The PostgreSQL strategy requires --archive and --filestore."
            raise ValueError(msg)
        validate_custom_dump(archive_path)
        validate_filestore(filestore_path)
    if plan.strategy is PrepareStrategy.ODOO and archive_path is not None:
        validate_native_runtime_archive(archive_path)

    # Restore or clone through one selected strategy so database and filestore decisions match.
    def restore() -> int | None:
        if plan.strategy is PrepareStrategy.POSTGRES:
            assert archive_path is not None
            assert filestore_path is not None
            return restore_custom_runtime(
                connection=config.db_connection,
                db_template="template0",
                dump_path=archive_path,
                filestore_source=filestore_path,
                data_dir=config.data_dir,
                force=force,
            )
        assert archive_path is not None
        return load_runtime_archive(
            db_name=config.db_name,
            archive_path=archive_path,
            odoo_bin_path=config.odoo_install_folder / "odoo-bin",
            odoo_conf_path=config.odoo_conf_path,
            data_dir=config.data_dir,
            force=force,
            connection=config.db_connection,
        )

    def clone() -> int:
        return duplicate_cow_runtime(
            source=source_db,
            target=config.db_name,
            force=force,
            odoo_main_path=config.odoo_install_folder,
            odoo_conf_path=config.odoo_conf_path,
            data_dir=config.data_dir,
            db_host=config.db_host,
            db_port=config.db_port,
            db_user=config.db_user,
            db_password=config.db_password,
            db_sslmode=config.db_connection.sslmode,
        )

    return prepare_runtime_pair(
        data_dir=config.data_dir,
        db_name=config.db_name,
        strategy=plan.strategy,
        cow_available=cow,
        postgres_archive=postgres,
        odoo_archive=odoo,
        clone=clone,
        restore=restore,
        bootstrap=lambda: ensure_runtime(config, allow_lifecycle_retry=True),
        source_db=source_db or None,
    )
