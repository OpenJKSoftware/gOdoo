"""High-level, testable gOdoo runtime lifecycle commands."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Optional

import typer

from ..cli_common import CommonCLI
from ..devcontainer_hooks import run_devcontainer_post_bootstrap_hooks
from ..lifecycle import LifecycleBootstrapError, ensure_runtime
from ..models import GodooConfig
from .db.passwords import set_passwords
from .odoo_bin.launch import launch_odoo
from .odoo_bin.shell import odoo_shell
from .source_get import sync_source

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def _runtime_config(
    *,
    odoo_main_path: Path,
    workspace_addon_path: Path,
    thirdparty_addon_path: Path,
    odoo_conf_path: Path,
    data_dir: Path,
    db_filter: str,
    db_name: str,
    db_user: str,
    db_host: str,
    db_port: int,
    db_password: str,
    multithread_worker_count: int,
    languages: str,
) -> GodooConfig:
    """Resolve common CLI inputs into one immutable runtime configuration."""
    return GodooConfig(
        odoo_install_folder=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        data_dir=data_dir,
        db_filter=db_filter,
        db_name=db_name,
        db_user=db_user,
        db_host=db_host,
        db_port=db_port,
        db_password=db_password,
        multithread_worker_count=multithread_worker_count,
        languages=languages,
    )


def _ensure_config(
    config: GodooConfig,
    *,
    sync_sources: bool,
    manifest_path: Optional[Path],
    thirdparty_zip_source: Optional[Path],
    odoo_demo: bool,
    extra_bootstrap_args: Optional[list[str]],
    install_workspace_modules: bool,
) -> bool:
    """Run the lifecycle service with CLI source-sync policy."""
    source_synchronizer: Optional[Callable[[], None]] = None
    if sync_sources:
        if manifest_path is None or thirdparty_zip_source is None:
            message = "--sync-sources requires ODOO_MANIFEST and ODOO_THIRDPARTY_ZIP_LOCATION."
            raise typer.BadParameter(message)

        def synchronize_source() -> None:
            sync_source(
                config,
                manifest_path=manifest_path,
                thirdparty_zip_source=thirdparty_zip_source,
                remove_unspecified_addons=True,
            )

        source_synchronizer = synchronize_source

    return ensure_runtime(
        config,
        source_synchronizer=source_synchronizer,
        odoo_demo=odoo_demo,
        extra_bootstrap_args=extra_bootstrap_args,
        install_workspace_modules=install_workspace_modules,
    )


def _run_devcontainer_hooks(
    config: GodooConfig,
    *,
    staging: bool,
    set_dev_password: bool,
    migrations_dir: Path,
) -> int:
    """Bind the DevContainer hook profile to gOdoo's existing safe commands."""
    shell_arguments = {
        "odoo_main_path": config.odoo_install_folder,
        "odoo_conf_path": config.odoo_conf_path,
        "db_name": config.db_name,
        "db_user": config.db_user,
        "db_host": config.db_host,
        "db_port": config.db_port,
        "db_password": config.db_password,
        "data_dir": config.data_dir,
    }

    def run_shell(code: str) -> int:
        return odoo_shell(pipe_in_command=code, **shell_arguments)

    def set_all_passwords() -> int:
        set_passwords(
            new_password="admin",
            db_name=config.db_name,
            db_user=config.db_user,
            db_host=config.db_host,
            db_port=config.db_port,
            db_password=config.db_password,
        )
        return 0

    return run_devcontainer_post_bootstrap_hooks(
        staging=staging,
        set_dev_password=set_dev_password,
        migrations_dir=migrations_dir,
        set_report_url=lambda: run_shell(
            "env['ir.config_parameter'].set_param('report.url', 'http://127.0.0.1:80'); env.cr.commit()"
        ),
        set_all_user_passwords=set_all_passwords,
        set_admin_login=lambda: run_shell("env['res.users'].browse(2).write({'login': 'admin'}); env.cr.commit()"),
        run_migration=lambda path: run_shell(path.read_text()),
    )


def ensure_odoo_runtime(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_filter: Annotated[str, CLI.database.db_filter],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    multithread_worker_count: Annotated[int, CLI.odoo_launch.multithread_worker_count] = 2,
    languages: Annotated[str, CLI.odoo_launch.languages] = "de_DE,en_US",
    sync_sources: Annotated[bool, typer.Option("--sync-sources/--no-sync-sources")] = False,
    manifest_path: Annotated[Optional[Path], CLI.source.mainfest_path] = None,
    thirdparty_zip_source: Annotated[
        Optional[Path], typer.Option(envvar="ODOO_THIRDPARTY_ZIP_LOCATION", help="Third-party addon archive directory")
    ] = None,
    odoo_demo: Annotated[bool, CLI.odoo_launch.odoo_demo] = False,
    extra_bootstrap_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
    install_workspace_modules: Annotated[bool, CLI.odoo_launch.install_workspace_modules] = True,
) -> int:
    """Prepare a runtime and bootstrap it only when Odoo reports it missing."""
    config = _runtime_config(
        odoo_main_path=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        odoo_conf_path=odoo_conf_path,
        data_dir=data_dir,
        db_filter=db_filter,
        db_name=db_name,
        db_user=db_user,
        db_host=db_host,
        db_port=db_port,
        db_password=db_password,
        multithread_worker_count=multithread_worker_count,
        languages=languages,
    )
    try:
        created = _ensure_config(
            config,
            sync_sources=sync_sources,
            manifest_path=manifest_path,
            thirdparty_zip_source=thirdparty_zip_source,
            odoo_demo=odoo_demo,
            extra_bootstrap_args=extra_bootstrap_args,
            install_workspace_modules=install_workspace_modules,
        )
    except LifecycleBootstrapError as error:
        LOGGER.exception("Native Odoo bootstrap failed")
        return CLI.returner(error.return_code)
    LOGGER.info("Runtime '%s' %s.", db_name, "bootstrapped" if created else "already ready")
    return CLI.returner(0)


def dev_odoo(
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_filter: Annotated[str, CLI.database.db_filter],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    data_dir: Annotated[Path, CLI.odoo_paths.data_dir] = Path("/var/lib/odoo"),
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    multithread_worker_count: Annotated[int, CLI.odoo_launch.multithread_worker_count] = 2,
    languages: Annotated[str, CLI.odoo_launch.languages] = "de_DE,en_US",
    sync_sources: Annotated[bool, typer.Option("--sync-sources/--no-sync-sources")] = False,
    manifest_path: Annotated[Optional[Path], CLI.source.mainfest_path] = None,
    thirdparty_zip_source: Annotated[
        Optional[Path], typer.Option(envvar="ODOO_THIRDPARTY_ZIP_LOCATION", help="Third-party addon archive directory")
    ] = None,
    odoo_demo: Annotated[bool, CLI.odoo_launch.odoo_demo] = False,
    extra_bootstrap_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
    install_workspace_modules: Annotated[bool, CLI.odoo_launch.install_workspace_modules] = True,
    dev_mode: Annotated[bool, CLI.odoo_launch.dev_mode] = False,
    extra_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args] = None,
    log_file_path: Annotated[Optional[Path], CLI.odoo_launch.log_file_path] = None,
    staging: Annotated[
        bool,
        typer.Option(
            "--stage/--no-stage",
            envvar="GODOO_LAUNCH_STAGE",
            help="Enable DevContainer staging post-bootstrap hooks",
        ),
    ] = False,
    set_dev_password: Annotated[
        bool,
        typer.Option(
            "--set-dev-password/--no-set-dev-password",
            envvar="GODOO_DEV_SET_PW",
            help="Set DevContainer admin credentials after bootstrap",
        ),
    ] = False,
    migrations_dir: Annotated[
        Path,
        typer.Option(
            envvar="GODOO_MIGRATIONS_DIR", help="Directory containing staging post-bootstrap migration scripts"
        ),
    ] = Path("scripts/migrations/staging"),
    no_launch: Annotated[
        bool, typer.Option("--no-launch", help="Ensure the runtime and run hooks without starting Odoo")
    ] = False,
) -> int:
    """Run the complete DevContainer lifecycle with Python as its sole owner."""
    config = _runtime_config(
        odoo_main_path=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        odoo_conf_path=odoo_conf_path,
        data_dir=data_dir,
        db_filter=db_filter,
        db_name=db_name,
        db_user=db_user,
        db_host=db_host,
        db_port=db_port,
        db_password=db_password,
        multithread_worker_count=multithread_worker_count,
        languages=languages,
    )
    try:
        created = _ensure_config(
            config,
            sync_sources=sync_sources,
            manifest_path=manifest_path,
            thirdparty_zip_source=thirdparty_zip_source,
            odoo_demo=odoo_demo,
            extra_bootstrap_args=extra_bootstrap_args,
            install_workspace_modules=install_workspace_modules,
        )
    except LifecycleBootstrapError as error:
        LOGGER.exception("Native Odoo bootstrap failed")
        return CLI.returner(error.return_code)

    if created:
        hook_result = _run_devcontainer_hooks(
            config,
            staging=staging,
            set_dev_password=set_dev_password,
            migrations_dir=migrations_dir,
        )
        if hook_result:
            return CLI.returner(hook_result)
    if no_launch:
        return CLI.returner(0)
    return launch_odoo(
        odoo_main_path=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        odoo_conf_path=odoo_conf_path,
        db_filter=db_filter,
        db_name=db_name,
        db_user=db_user,
        data_dir=data_dir,
        db_host=db_host,
        db_port=db_port,
        db_password=db_password,
        extra_args=extra_args,
        log_file_path=log_file_path,
        dev_mode=dev_mode,
        multithread_worker_count=multithread_worker_count,
        languages=languages,
    )
