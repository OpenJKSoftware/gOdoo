"""High-level, testable gOdoo runtime lifecycle commands."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Optional

import psycopg2
import typer

from ..cli_common import CommonCLI
from ..devcontainer_hooks import run_devcontainer_post_bootstrap_hooks
from ..helpers.odoo_command import run_odoo_command
from ..lifecycle import LifecycleBootstrapError, deployment_init, ensure_runtime, reconcile_runtime
from ..models import GodooConfig
from .db.archive import load_legacy_runtime_dump, load_runtime_archive
from .db.passwords import set_passwords
from .odoo_bin.cli_generate import _launch_command
from .odoo_bin.launch import launch_odoo, prepare_runtime
from .odoo_bin.shell import odoo_shell
from .source_get import py_depends_by_db, sync_source

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


def _source_synchronizer(config: GodooConfig, manifest_path: Optional[Path], thirdparty_zip_source: Optional[Path]):
    """Return an explicit source synchronizer or reject incomplete source inputs."""
    if manifest_path is None or thirdparty_zip_source is None:
        message = "--sync-sources requires ODOO_MANIFEST and ODOO_THIRDPARTY_ZIP_LOCATION."
        raise typer.BadParameter(message)
    return lambda: sync_source(
        config, manifest_path=manifest_path, thirdparty_zip_source=thirdparty_zip_source, remove_unspecified_addons=True
    )


def _run_hook(config: GodooConfig, script: Path) -> int:
    """Execute a deployment policy hook through Odoo shell, never a system shell."""
    return odoo_shell(
        odoo_main_path=config.odoo_install_folder,
        odoo_conf_path=config.odoo_conf_path,
        db_name=config.db_name,
        db_user=config.db_user,
        db_host=config.db_host,
        db_port=config.db_port,
        db_password=config.db_password,
        data_dir=config.data_dir,
        addon_paths=config.addon_paths,
        pipe_in_command=script.read_text(),
    )


def _split_values(values: Optional[list[str]]) -> list[str]:
    """Flatten repeatable comma-separated values, preserving first-seen order."""
    return list(dict.fromkeys(item.strip() for value in values or [] for item in value.split(",") if item.strip()))


def _reconcile_modules(
    config: GodooConfig,
    update_modules: Optional[list[str]],
    install_modules: Optional[list[str]],
    *,
    upgrade_path: Optional[Path] = None,
    pre_upgrade_scripts: Optional[list[Path]] = None,
    log_handlers: Optional[list[str]] = None,
) -> int:
    """Run explicitly requested module actions and stop; no action is implicit."""
    updates = _split_values(update_modules)
    installs = _split_values(install_modules)
    scripts = list(dict.fromkeys(pre_upgrade_scripts or []))
    handlers = _split_values(log_handlers)
    if (upgrade_path or scripts) and not updates:
        message = "--upgrade-path and --pre-upgrade-script require at least one --update module."
        raise ValueError(message)
    extra: list[str] = ["--stop-after-init"]
    if updates:
        extra.extend(["--update", ",".join(updates)])
    if installs:
        extra.extend(["--init", ",".join(installs)])
    if not updates and not installs:
        return 0
    if upgrade_path:
        extra.extend(["--upgrade-path", str(upgrade_path)])
    if scripts:
        extra.extend(["--pre-upgrade-scripts", ",".join(str(script) for script in scripts)])
    for handler in handlers:
        extra.extend(["--log-handler", handler])
    if not config.odoo_conf_path.exists():
        extra.extend(["--addons-path", ",".join(str(path.absolute()) for path in config.addon_paths)])
    return run_odoo_command(_launch_command(config, extra, upgrade_workspace_modules=False)).returncode


def _after_reconcile_directories(
    after_reconcile_dirs: Optional[list[Path]], pre_launch_hooks_dir: Optional[Path]
) -> list[Path]:
    """Merge the deprecated pre-launch alias after canonical directories."""
    directories = list(after_reconcile_dirs or [])
    if pre_launch_hooks_dir is not None:
        LOGGER.warning(
            "--pre-launch-hooks-dir/GODOO_PRE_LAUNCH_HOOKS_DIR is deprecated; use "
            "--after-reconcile-dir/GODOO_AFTER_RECONCILE_DIRS."
        )
        directories.append(pre_launch_hooks_dir)
    return directories


def _selected_seed(seed: Optional[Path], seed_archive: Optional[Path]) -> Optional[Path]:
    """Resolve the canonical seed and its deprecated archive-only alias."""
    if seed is not None and seed_archive is not None and seed != seed_archive:
        message = "Use either --seed or deprecated --seed-archive, not both."
        raise typer.BadParameter(message)
    if seed_archive is not None:
        LOGGER.warning("--seed-archive/GODOO_SEED_ARCHIVE is deprecated; use --seed/GODOO_RUNTIME_SEED.")
    return seed if seed is not None else seed_archive


def _resolve_installed_dependencies(config: GodooConfig) -> int:
    return (
        py_depends_by_db(
            odoo_main_path=config.odoo_install_folder,
            workspace_addon_path=config.workspace_addon_path,
            thirdparty_addon_path=config.thirdparty_addon_path,
            db_name=config.db_name,
            db_user=config.db_user,
            db_host=config.db_host,
            db_port=config.db_port,
            db_password=config.db_password,
        )
        or 0
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
    except ValueError:
        LOGGER.exception("Runtime bootstrap precondition failed")
        return CLI.returner(1)
    LOGGER.info("Runtime '%s' %s.", db_name, "bootstrapped" if created else "already ready")
    return CLI.returner(0)


def bootstrap_odoo_runtime(
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
    odoo_demo: Annotated[bool, CLI.odoo_launch.odoo_demo] = False,
    extra_bootstrap_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
    install_workspace_modules: Annotated[bool, CLI.odoo_launch.install_workspace_modules] = True,
    install_base_modules: Annotated[
        bool, typer.Option(envvar="GODOO_INSTALL_BASE_MODULES", help="Install base/web when bootstrapping.")
    ] = True,
) -> int:
    """Initialize only a missing or empty runtime after explicit preparation."""
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
        created = ensure_runtime(
            config,
            preparer=lambda _config: None,
            odoo_demo=odoo_demo,
            extra_bootstrap_args=extra_bootstrap_args,
            install_workspace_modules=install_workspace_modules,
            install_base_modules=install_base_modules,
            prepare_bootstrap_dependencies=False,
        )
    except LifecycleBootstrapError as error:
        LOGGER.exception("Native Odoo bootstrap failed")
        return CLI.returner(error.return_code)
    except ValueError:
        LOGGER.exception("Runtime bootstrap precondition failed")
        return CLI.returner(1)
    LOGGER.info("Runtime '%s' %s.", db_name, "bootstrapped" if created else "already ready")
    return CLI.returner(0)


def reconcile_odoo_runtime(
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
    sync_sources: Annotated[
        bool, typer.Option("--sync-sources/--no-sync-sources", envvar="GODOO_RECONCILE_SYNC_SOURCES")
    ] = False,
    manifest_path: Annotated[Optional[Path], CLI.source.mainfest_path] = None,
    thirdparty_zip_source: Annotated[Optional[Path], typer.Option(envvar="ODOO_THIRDPARTY_ZIP_LOCATION")] = None,
    resolve_installed_dependencies: Annotated[bool, typer.Option(envvar="GODOO_RECONCILE_DEPENDENCIES")] = False,
    update_modules: Annotated[
        Optional[list[str]],
        typer.Option("--update", envvar="GODOO_RECONCILE_UPDATE", help="Module(s) to update; repeat or use commas."),
    ] = None,
    install_modules: Annotated[
        Optional[list[str]],
        typer.Option("--install", envvar="GODOO_RECONCILE_INSTALL", help="Module(s) to install; repeat or use commas."),
    ] = None,
    upgrade_path: Annotated[
        Optional[Path], typer.Option("--upgrade-path", envvar="GODOO_RECONCILE_UPGRADE_PATH")
    ] = None,
    pre_upgrade_scripts: Annotated[
        Optional[list[Path]],
        typer.Option("--pre-upgrade-script", envvar="GODOO_RECONCILE_PRE_UPGRADE_SCRIPTS"),
    ] = None,
    log_handlers: Annotated[
        Optional[list[str]], typer.Option("--log-handler", envvar="GODOO_RECONCILE_LOG_HANDLERS")
    ] = None,
    x_sendfile: Annotated[
        Optional[bool], typer.Option("--x-sendfile/--no-x-sendfile", envvar="GODOO_X_SENDFILE")
    ] = None,
    after_reconcile_dirs: Annotated[
        Optional[list[Path]],
        typer.Option("--after-reconcile-dir", envvar="GODOO_AFTER_RECONCILE_DIRS"),
    ] = None,
    pre_launch_hooks_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--pre-launch-hooks-dir",
            envvar="GODOO_PRE_LAUNCH_HOOKS_DIR",
            help="Lexically ordered *.py Odoo-shell hooks run after reconciliation.",
        ),
    ] = None,
) -> int:
    """Reconcile an existing runtime; it never restores, bootstraps, or resets."""
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
        result = reconcile_runtime(
            config,
            source_synchronizer=_source_synchronizer(config, manifest_path, thirdparty_zip_source)
            if sync_sources
            else None,
            dependency_resolver=_resolve_installed_dependencies if resolve_installed_dependencies else None,
            preparer=lambda conf: prepare_runtime(conf, x_sendfile=x_sendfile),
            reconciler=lambda conf: _reconcile_modules(
                conf,
                update_modules,
                install_modules,
                upgrade_path=upgrade_path,
                pre_upgrade_scripts=pre_upgrade_scripts,
                log_handlers=log_handlers,
            ),
            after_reconcile_dirs=_after_reconcile_directories(after_reconcile_dirs, pre_launch_hooks_dir),
            hook_runner=_run_hook,
        )
    except ValueError:
        LOGGER.exception("Runtime reconciliation failed")
        return CLI.returner(1)
    return CLI.returner(result)


def deployment_init_odoo_runtime(
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
    seed: Annotated[
        Optional[Path],
        typer.Option("--seed", envvar="GODOO_RUNTIME_SEED", help="Native Odoo ZIP or legacy dump directory."),
    ] = None,
    seed_archive: Annotated[
        Optional[Path],
        typer.Option(
            "--seed-archive",
            envvar="GODOO_SEED_ARCHIVE",
            help="Odoo ZIP archive loaded only when the runtime is missing or empty.",
        ),
    ] = None,
    sync_sources: Annotated[
        bool, typer.Option("--sync-sources/--no-sync-sources", envvar="GODOO_RECONCILE_SYNC_SOURCES")
    ] = False,
    manifest_path: Annotated[Optional[Path], CLI.source.mainfest_path] = None,
    thirdparty_zip_source: Annotated[Optional[Path], typer.Option(envvar="ODOO_THIRDPARTY_ZIP_LOCATION")] = None,
    resolve_installed_dependencies: Annotated[bool, typer.Option(envvar="GODOO_RECONCILE_DEPENDENCIES")] = False,
    update_modules: Annotated[Optional[list[str]], typer.Option("--update", envvar="GODOO_RECONCILE_UPDATE")] = None,
    install_modules: Annotated[Optional[list[str]], typer.Option("--install", envvar="GODOO_RECONCILE_INSTALL")] = None,
    upgrade_path: Annotated[
        Optional[Path], typer.Option("--upgrade-path", envvar="GODOO_RECONCILE_UPGRADE_PATH")
    ] = None,
    pre_upgrade_scripts: Annotated[
        Optional[list[Path]],
        typer.Option("--pre-upgrade-script", envvar="GODOO_RECONCILE_PRE_UPGRADE_SCRIPTS"),
    ] = None,
    log_handlers: Annotated[
        Optional[list[str]], typer.Option("--log-handler", envvar="GODOO_RECONCILE_LOG_HANDLERS")
    ] = None,
    x_sendfile: Annotated[
        Optional[bool], typer.Option("--x-sendfile/--no-x-sendfile", envvar="GODOO_X_SENDFILE")
    ] = None,
    after_bootstrap_dirs: Annotated[
        Optional[list[Path]], typer.Option("--after-bootstrap-dir", envvar="GODOO_AFTER_BOOTSTRAP_DIRS")
    ] = None,
    after_restore_dirs: Annotated[
        Optional[list[Path]], typer.Option("--after-restore-dir", envvar="GODOO_AFTER_RESTORE_DIRS")
    ] = None,
    after_reconcile_dirs: Annotated[
        Optional[list[Path]], typer.Option("--after-reconcile-dir", envvar="GODOO_AFTER_RECONCILE_DIRS")
    ] = None,
    pre_launch_hooks_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--pre-launch-hooks-dir",
            envvar="GODOO_PRE_LAUNCH_HOOKS_DIR",
            help="Lexically ordered *.py Odoo-shell hooks run after reconciliation.",
        ),
    ] = None,
    install_base_modules: Annotated[
        bool, typer.Option(envvar="GODOO_INSTALL_BASE_MODULES", help="Install base/web when bootstrapping.")
    ] = True,
    install_workspace_modules: Annotated[bool, CLI.odoo_launch.install_workspace_modules] = True,
    odoo_demo: Annotated[
        bool,
        typer.Option(
            "--odoo-demo/--no-odoo-demo", envvar="GODOO_RUNTIME_DEMO", help="Load demo data when bootstrapping."
        ),
    ] = False,
    extra_bootstrap_args: Annotated[Optional[list[str]], CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
    db_template: Annotated[str, CLI.database.db_template_name] = "template0",
) -> int:
    """One-shot init: seed or bootstrap, reconcile, run phase hooks, then exit."""
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
    source_sync = _source_synchronizer(config, manifest_path, thirdparty_zip_source) if sync_sources else None
    runtime_seed = _selected_seed(seed, seed_archive)

    def already_prepared(_conf: GodooConfig) -> None:
        return None

    def ensure(conf: GodooConfig) -> bool:
        return ensure_runtime(
            conf,
            preparer=already_prepared,
            odoo_demo=odoo_demo,
            extra_bootstrap_args=extra_bootstrap_args,
            install_workspace_modules=install_workspace_modules,
            install_base_modules=install_base_modules,
            prepare_bootstrap_dependencies=False,
        )

    def seed_runtime(conf: GodooConfig) -> None:
        assert runtime_seed is not None
        if runtime_seed.is_dir():
            load_legacy_runtime_dump(
                db_name=conf.db_name,
                source_folder=runtime_seed,
                data_dir=conf.data_dir,
                db_host=conf.db_host,
                db_port=conf.db_port,
                db_user=conf.db_user,
                db_password=conf.db_password,
                db_template=db_template,
            )
            return
        result = load_runtime_archive(
            db_name=conf.db_name,
            archive_path=runtime_seed,
            odoo_bin_path=conf.odoo_install_folder / "odoo-bin",
            odoo_conf_path=conf.odoo_conf_path,
            data_dir=conf.data_dir,
            force=True,
        )
        if result:
            message = f"Odoo seed archive load failed for runtime '{conf.db_name}' (exit code {result})"
            raise RuntimeError(message)

    try:
        outcome, result = deployment_init(
            config,
            seed_requested=runtime_seed is not None,
            seeder=seed_runtime,
            ensure=ensure,
            source_synchronizer=source_sync,
            preparer=lambda conf: prepare_runtime(conf, x_sendfile=x_sendfile),
            reconciler=lambda conf: reconcile_runtime(
                conf,
                preparer=already_prepared,
                dependency_resolver=_resolve_installed_dependencies if resolve_installed_dependencies else None,
                reconciler=lambda runtime: _reconcile_modules(
                    runtime,
                    update_modules,
                    install_modules,
                    upgrade_path=upgrade_path,
                    pre_upgrade_scripts=pre_upgrade_scripts,
                    log_handlers=log_handlers,
                ),
            ),
            after_bootstrap_dirs=after_bootstrap_dirs,
            after_restore_dirs=after_restore_dirs,
            after_reconcile_dirs=_after_reconcile_directories(after_reconcile_dirs, pre_launch_hooks_dir),
            hook_runner=_run_hook,
        )
    except (ValueError, RuntimeError, OSError, psycopg2.Error):
        LOGGER.exception("Deployment initialization failed")
        return CLI.returner(1)
    LOGGER.info("Runtime initialization outcome: %s", outcome.value)
    return CLI.returner(result)


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
    except ValueError:
        LOGGER.exception("Runtime bootstrap precondition failed")
        return CLI.returner(1)

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
