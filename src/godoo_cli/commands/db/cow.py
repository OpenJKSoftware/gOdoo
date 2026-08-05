"""Opt-in copy-on-write Odoo database and filestore duplication."""

import logging
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Annotated, Optional

import typer

from ...cli_common import CommonCLI

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()

ReflinkRunner = Callable[[Sequence[str]], None]


@dataclass(frozen=True)
class CowPreflight:
    """CoW prerequisites and their actionable failures."""

    errors: tuple[str, ...]

    @property
    def available(self) -> bool:
        """Return whether every required capability is available."""
        return not self.errors


def check_cow_preflight(
    *,
    source: str,
    target: str,
    force: bool,
    source_database_exists: bool,
    target_database_exists: bool,
    source_filestore: Path,
    target_filestore: Path,
    server_version_num: int,
    file_copy_method: str,
    reflink_utility_available: bool,
) -> CowPreflight:
    """Validate prerequisites without creating, dropping, or copying anything."""
    errors = []
    if source == target:
        errors.append("Source and target database names must differ.")
    if server_version_num < 180000:
        errors.append("PostgreSQL 18 or newer is required for a CoW database clone.")
    if file_copy_method != "clone":
        errors.append("PostgreSQL file_copy_method must be configured as 'clone'.")
    if not source_database_exists:
        errors.append(f"Source database '{source}' does not exist.")
    if not source_filestore.is_dir():
        errors.append(f"Source filestore does not exist: {source_filestore}")
    if not force and target_database_exists:
        errors.append(f"Target database '{target}' already exists; pass --force to replace it.")
    if not force and target_filestore.exists():
        errors.append(f"Target filestore already exists: {target_filestore}; pass --force to replace it.")
    if not reflink_utility_available:
        errors.append("The 'cp' utility is required for a strict reflink filestore clone.")
    return CowPreflight(errors=tuple(errors))


def _run_reflink_copy(command: Sequence[str]) -> None:
    """Clone a filestore, refusing a regular-copy fallback."""
    subprocess.run(list(command), check=True)


def _configure_odoo_runtime(
    *,
    odoo_main_path: Path,
    odoo_conf_path: Optional[Path],
    data_dir: Path,
    db_host: str,
    db_port: int,
    db_user: str,
    db_password: str,
) -> ModuleType:
    """Load Odoo only when the state-changing command is actually invoked."""
    odoo_path = str(odoo_main_path)
    if odoo_path not in sys.path:
        sys.path.insert(0, odoo_path)
    import odoo  # pylint: disable=import-outside-toplevel
    from odoo.tools import config  # pylint: disable=import-outside-toplevel

    arguments = ["--data-dir", str(data_dir)]
    if odoo_conf_path is not None:
        arguments.extend(["--config", str(odoo_conf_path)])
    for option, value in (
        ("db_host", db_host),
        ("db_port", str(db_port) if db_port else ""),
        ("db_user", db_user),
        ("db_password", db_password),
    ):
        if value:
            arguments.extend([f"--{option}", value])
    config.parse_config(arguments, setup_logging=False)
    config["list_db"] = True
    return odoo


def _server_settings(odoo: ModuleType) -> tuple[int, str]:
    """Read the PostgreSQL features required by this command."""
    database = odoo.sql_db.db_connect("postgres")
    with closing(database.cursor()) as cursor:
        cursor.execute("SHOW server_version_num")
        version = int(cursor.fetchone()[0])
        cursor.execute("SHOW file_copy_method")
        method = str(cursor.fetchone()[0])
    return version, method


def _remove_target_pair(_odoo: ModuleType, target: str, target_filestore: Path) -> None:
    """Use Odoo's drop path, then remove an orphaned target filestore if present."""
    from odoo.service import db as odoo_db  # pylint: disable=import-outside-toplevel

    if odoo_db.exp_db_exist(target):
        odoo_db.exp_drop(target)
    if target_filestore.exists():
        shutil.rmtree(target_filestore)


def _create_database_clone(odoo: ModuleType, source: str, target: str) -> None:
    """Create the DB clone, tracking Odoo 19 ``exp_duplicate_database``."""
    from odoo.service import db as odoo_db  # pylint: disable=import-outside-toplevel
    from odoo.tools import SQL  # pylint: disable=import-outside-toplevel

    odoo.sql_db.close_db(source)
    database = odoo.sql_db.db_connect("postgres")
    with closing(database.cursor()) as cursor:
        cursor._cnx.autocommit = True
        odoo_db._drop_conn(cursor, source)  # pylint: disable=protected-access
        cursor.execute(
            SQL(
                "CREATE DATABASE %s ENCODING 'unicode' TEMPLATE %s STRATEGY FILE_COPY",
                odoo_db.database_identifier(cursor, target),
                odoo_db.database_identifier(cursor, source),
            )
        )


def _initialize_duplicate_uuid(odoo: ModuleType, target: str) -> None:
    """Assign the duplicated database a new UUID, as Odoo's duplicate command does."""
    registry = odoo.modules.registry.Registry.new(target)
    with registry.cursor() as cursor:
        environment = odoo.api.Environment(cursor, odoo.api.SUPERUSER_ID, {})
        environment["ir.config_parameter"].init(force=True)


def duplicate_cow_runtime(
    *,
    source: str,
    target: str,
    force: bool,
    odoo_main_path: Path,
    odoo_conf_path: Optional[Path],
    data_dir: Path,
    db_host: str,
    db_port: int,
    db_user: str,
    db_password: str,
    odoo_loader: Callable[..., ModuleType] = _configure_odoo_runtime,
    reflink_runner: ReflinkRunner = _run_reflink_copy,
) -> int:
    """Create a strict CoW clone of an Odoo database and its filestore."""
    try:
        odoo = odoo_loader(
            odoo_main_path=odoo_main_path,
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
        )
        from odoo.service import db as odoo_db  # pylint: disable=import-outside-toplevel
        from odoo.tools import config  # pylint: disable=import-outside-toplevel

        source_filestore = Path(config.filestore(source))
        target_filestore = Path(config.filestore(target))
        version, copy_method = _server_settings(odoo)
        preflight = check_cow_preflight(
            source=source,
            target=target,
            force=force,
            source_database_exists=odoo_db.exp_db_exist(source),
            target_database_exists=odoo_db.exp_db_exist(target),
            source_filestore=source_filestore,
            target_filestore=target_filestore,
            server_version_num=version,
            file_copy_method=copy_method,
            reflink_utility_available=shutil.which("cp") is not None,
        )
    except Exception:
        LOGGER.exception("Unable to determine CoW clone capabilities.")
        return 1
    if not preflight.available:
        for error in preflight.errors:
            LOGGER.error("CoW clone unavailable: %s", error)
        return 2
    try:
        if force:
            _remove_target_pair(odoo, target, target_filestore)
        _create_database_clone(odoo, source, target)
        _initialize_duplicate_uuid(odoo, target)
        reflink_runner(["cp", "-a", "--reflink=always", str(source_filestore), str(target_filestore)])
    except Exception:
        LOGGER.exception("CoW clone failed; removing target database and filestore: %s", target)
        try:
            _remove_target_pair(odoo, target, target_filestore)
        except Exception:
            LOGGER.exception("Could not fully clean failed CoW clone target: %s", target)
        return 1
    LOGGER.info("Created CoW clone '%s' from '%s'.", target, source)
    return 0


def duplicate_cow(
    source: Annotated[str, typer.Argument(help="Source Odoo database")],
    target: Annotated[str, typer.Argument(help="New Odoo database name")],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    db_user: Annotated[str, CLI.database.db_user],
    force: Annotated[
        bool,
        typer.Option("--force", envvar="ODOO_DB_DUPLICATE_COW_FORCE", help="Replace an existing target pair"),
    ] = False,
    odoo_conf_path: Annotated[Optional[Path], CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
) -> int:
    """Duplicate an Odoo database and filestore using strict CoW cloning."""
    return CLI.returner(
        duplicate_cow_runtime(
            source=source,
            target=target,
            force=force,
            odoo_main_path=odoo_main_path,
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
        )
    )
