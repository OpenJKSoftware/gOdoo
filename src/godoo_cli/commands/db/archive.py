"""Thin wrappers around Odoo's database archive commands."""

import logging
import os
import uuid
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Optional

import psycopg2
import typer

from ...cli_common import CommonCLI
from ...helpers.odoo_files import require_odoo_version
from ...models import DBConnection
from .reset import odoo_db_command
from .restore import RuntimeRestoreError, restore_custom_runtime

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()

CommandRunner = Callable[[Sequence[str]], int]

LEGACY_DUMP_FILENAME = "odoo.dump"
LEGACY_FILESTORE_DIRECTORY = "odoo_filestore"


def _run_odoo_db(command: Sequence[str]) -> int:
    """Run an Odoo database archive command without invoking a shell."""
    from ...helpers.odoo_command import run_odoo_command

    LOGGER.info("Running Odoo database command: %s", " ".join(command))
    return run_odoo_command(command).returncode


def dump_runtime_archive(
    *,
    db_name: str,
    archive_path: Path,
    odoo_bin_path: Path,
    odoo_conf_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    runner: CommandRunner = _run_odoo_db,
) -> int:
    """Create an archive atomically, retaining any prior destination on failure."""
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_name(f".{archive_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        result = runner(
            odoo_db_command(
                odoo_bin_path=odoo_bin_path,
                odoo_conf_path=odoo_conf_path,
                data_dir=data_dir,
                arguments=["dump", db_name, str(temporary)],
            )
        )
        if result:
            return result
        os.replace(temporary, archive_path)
        return 0
    finally:
        temporary.unlink(missing_ok=True)


def validate_native_runtime_archive(archive_path: Path) -> None:
    """Validate Odoo's ZIP structure and CRCs before allowing a forced load."""
    if not archive_path.is_file():
        message = f"Odoo runtime archive does not exist or is not a file: {archive_path}"
        raise RuntimeRestoreError(message)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if "dump.sql" not in archive.namelist():
                message = f"Odoo runtime archive does not contain dump.sql: {archive_path}"
                raise RuntimeRestoreError(message)
            if bad_member := archive.testzip():
                message = f"Odoo runtime archive contains a corrupt member '{bad_member}': {archive_path}"
                raise RuntimeRestoreError(message)
    except zipfile.BadZipFile as error:
        message = f"Odoo runtime archive is not a valid ZIP file: {archive_path}"
        raise RuntimeRestoreError(message) from error


def load_runtime_archive(
    *,
    db_name: str,
    archive_path: Path,
    odoo_bin_path: Path,
    odoo_conf_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    force: bool = False,
    runner: CommandRunner = _run_odoo_db,
) -> int:
    """Load an Odoo ZIP archive, including only that archive's filestore."""
    validate_native_runtime_archive(archive_path)
    arguments = ["load"]
    if force:
        arguments.append("--force")
    arguments.extend([db_name, str(archive_path)])
    return runner(
        odoo_db_command(
            odoo_bin_path=odoo_bin_path,
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
            arguments=arguments,
        )
    )


def _legacy_filestore_source(source_folder: Path, db_name: str) -> Path:
    """Select the matching filestore from a gOdoo 0.17 dump directory."""
    filestore_root = source_folder / LEGACY_FILESTORE_DIRECTORY / "filestore"
    expected = filestore_root / db_name
    if expected.is_dir():
        return expected
    candidates = sorted(path for path in filestore_root.iterdir() if path.is_dir()) if filestore_root.is_dir() else []
    if len(candidates) == 1:
        return candidates[0]
    message = (
        f"Legacy dump '{source_folder}' does not contain one unambiguous filestore for database '{db_name}'. "
        "Restore with the original database name or keep only that filestore in the dump."
    )
    raise RuntimeRestoreError(message)


def load_legacy_runtime_dump(
    *,
    db_name: str,
    source_folder: Path,
    data_dir: Path,
    db_host: str,
    db_port: int,
    db_user: str,
    db_password: str,
    db_template: str,
) -> None:
    """Load the directory format emitted by gOdoo 0.17's ``backup dump`` command."""
    restore_custom_runtime(
        connection=DBConnection(db_host, db_port, db_user, db_password, db_name),
        db_template=db_template,
        dump_path=source_folder / LEGACY_DUMP_FILENAME,
        filestore_source=_legacy_filestore_source(source_folder, db_name),
        data_dir=data_dir,
    )


def dump_database(
    db_name: Annotated[str, CLI.database.db_name],
    archive_path: Annotated[
        Path,
        typer.Argument(help="Destination Odoo ZIP archive.", envvar="GODOO_ARCHIVE_PATH"),
    ],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Optional[Path], CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
) -> int:
    """Create an Odoo-native database and filestore archive."""
    require_odoo_version(odoo_main_path, ">=19")
    try:
        return CLI.returner(
            dump_runtime_archive(
                db_name=db_name,
                archive_path=archive_path,
                odoo_bin_path=odoo_main_path / "odoo-bin",
                odoo_conf_path=odoo_conf_path,
                data_dir=data_dir,
            )
        )
    except OSError:
        LOGGER.exception("Atomic Odoo archive creation failed")
        return CLI.returner(1)


def load_database(
    db_name: Annotated[str, CLI.database.db_name],
    archive_path: Annotated[
        Path,
        typer.Argument(help="Source Odoo ZIP archive or gOdoo 0.17 dump directory.", envvar="GODOO_ARCHIVE_PATH"),
    ],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    odoo_conf_path: Annotated[Optional[Path], CLI.odoo_paths.conf_path] = None,
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    db_user: Annotated[str, CLI.database.db_user] = "",
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    force: Annotated[
        bool,
        typer.Option(
            "--force", envvar="GODOO_DB_LOAD_FORCE", help="Allow replacement when loading an existing or legacy dump."
        ),
    ] = False,
    db_template: Annotated[str, CLI.database.db_template_name] = "template0",
) -> int:
    """Load an Odoo ZIP archive or a gOdoo 0.17 dump directory."""
    require_odoo_version(odoo_main_path, ">=19")
    if archive_path.is_dir():
        if not force:
            message = "Loading a legacy gOdoo 0.17 dump directory replaces the target runtime; pass --force."
            LOGGER.error(message)
            return CLI.returner(2)
        try:
            load_legacy_runtime_dump(
                db_name=db_name,
                source_folder=archive_path,
                data_dir=data_dir,
                db_host=db_host,
                db_port=db_port,
                db_user=db_user,
                db_password=db_password,
                db_template=db_template,
            )
        except (RuntimeRestoreError, OSError, psycopg2.Error):
            LOGGER.exception("Legacy gOdoo dump load failed")
            return CLI.returner(1)
        return CLI.returner(0)
    try:
        result = load_runtime_archive(
            db_name=db_name,
            archive_path=archive_path,
            odoo_bin_path=odoo_main_path / "odoo-bin",
            odoo_conf_path=odoo_conf_path,
            data_dir=data_dir,
            force=force,
        )
    except (RuntimeRestoreError, OSError):
        LOGGER.exception("Odoo runtime archive load failed")
        return CLI.returner(1)
    return CLI.returner(result)
