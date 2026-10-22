"""High-level, testable gOdoo runtime lifecycle commands."""

import logging
import os
from pathlib import Path
from typing import Annotated

import psycopg2
import typer

from ...models import GodooConfig
from ...runtime.archive import load_runtime_archive, prepare_archive_with_original_filestore
from ...runtime.dataset import resolve_runtime_dataset
from ...runtime.lifecycle import (
    LifecycleBootstrapError,
    deployment_init,
    ensure_runtime,
    preflight_reconcile_dependencies,
    reconcile_modules,
    reconcile_runtime,
    run_lifecycle_hook,
    split_lifecycle_values,
)
from ...runtime.migration import resolve_migration_directory
from ...runtime.nginx import write_nginx_static_config
from ...runtime.odoo import (
    SUPPORTED_ODOO_VERSION_SPECIFIER,
    OdooVersionError,
    prepare_runtime,
    require_odoo_version,
    set_report_url,
    x_sendfile_enabled,
)
from ...runtime.restore import restore_custom_runtime
from ..common import CommonCLI
from ..configuration import resolve_command_config

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def _validate_pre_upgrade_options(
    seed: Path | None,
    pre_upgrade_scripts: list[Path] | None,
    update_modules: list[str] | None,
    after_restore_dirs: list[Path] | None,
) -> None:
    """Reject hook combinations that would start Odoo before pre-upgrade scripts."""
    if seed is not None and pre_upgrade_scripts and after_restore_dirs:
        message = "--after-restore-dir cannot run with --pre-upgrade-script because restore hooks start Odoo first."
        raise typer.BadParameter(message, param_hint="--after-restore-dir")
    if pre_upgrade_scripts and not split_lifecycle_values(update_modules):
        message = "--pre-upgrade-script requires at least one --update module."
        raise typer.BadParameter(message, param_hint="--update")


def _validate_migration_overrides(
    update_modules: list[str] | None,
    install_modules: list[str] | None,
    pre_upgrade_scripts: list[Path] | None,
    after_bootstrap_dirs: list[Path] | None,
    after_restore_dirs: list[Path] | None,
) -> None:
    """Prevent generic defaults from changing a selected migration."""
    for variable in (
        "GODOO_RECONCILE_UPDATE",
        "GODOO_RECONCILE_INSTALL",
        "GODOO_RECONCILE_PRE_UPGRADE_SCRIPTS",
        "GODOO_RECONCILE_UPGRADE_PATH",
        "GODOO_AFTER_BOOTSTRAP_DIRS",
        "GODOO_AFTER_RESTORE_DIRS",
        "GODOO_AFTER_RECONCILE_DIRS",
    ):
        if os.environ.get(variable, "").strip():
            message = f"Unset {variable} when selecting --migration-dir; the transition supplies those inputs."
            raise typer.BadParameter(message, param_hint="--migration-dir")
    for option, value in (
        ("--update", update_modules),
        ("--install", install_modules),
        ("--pre-upgrade-script", pre_upgrade_scripts),
        ("--after-bootstrap-dir", after_bootstrap_dirs),
        ("--after-restore-dir", after_restore_dirs),
    ):
        if value:
            message = f"{option} cannot be combined with --migration-dir."
            raise typer.BadParameter(message, param_hint=option)


# Keep validation and execution ordering visible at this public command boundary.
def deployment_init_odoo_runtime(  # noqa: C901
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_filter: Annotated[str, CLI.database.db_filter],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    data_dir: Annotated[Path | None, CLI.odoo_paths.data_dir] = None,
    db_host: Annotated[str | None, CLI.database.db_host] = None,
    db_port: Annotated[int | None, CLI.database.db_port] = None,
    db_password: Annotated[str | None, CLI.database.db_password] = None,
    multithread_worker_count: Annotated[int, CLI.odoo_launch.multithread_worker_count] = 2,
    languages: Annotated[str, CLI.odoo_launch.languages] = "de_DE,en_US",
    seed: Annotated[
        Path | None,
        typer.Option("--seed", envvar="GODOO_RUNTIME_SEED", help="Native Odoo ZIP archive."),
    ] = None,
    dump_path: Annotated[
        Path | None,
        typer.Option(
            "--dump-path",
            envvar="GODOO_DUMP_PATH",
            help="Fixed dataset directory containing odoo.dump and odoo_filestore.",
        ),
    ] = None,
    keep_reset_baseline: Annotated[bool, CLI.database.keep_reset_baseline] = False,
    restore_jobs: Annotated[
        int,
        typer.Option(
            "--restore-jobs",
            envvar="GODOO_RESTORE_JOBS",
            min=1,
            help="Parallel pg_restore jobs for custom-format dataset imports.",
        ),
    ] = 1,
    migration_dir: Annotated[
        Path | None,
        typer.Option(
            "--migration-dir",
            envvar="GODOO_MIGRATION_DIR",
            help="Migration transition directory; relative paths start at the workspace working directory.",
        ),
    ] = None,
    original_filestore: Annotated[
        Path | None,
        typer.Option("--original-filestore", envvar="GODOO_ORIGINAL_FILESTORE"),
    ] = None,
    update_modules: Annotated[list[str] | None, typer.Option("--update", envvar="GODOO_RECONCILE_UPDATE")] = None,
    install_modules: Annotated[list[str] | None, typer.Option("--install", envvar="GODOO_RECONCILE_INSTALL")] = None,
    upgrade_path: Annotated[
        list[Path] | None,
        typer.Option("--upgrade-path", envvar="GODOO_RECONCILE_UPGRADE_PATH"),
    ] = None,
    pre_upgrade_scripts: Annotated[
        list[Path] | None,
        typer.Option("--pre-upgrade-script", envvar="GODOO_RECONCILE_PRE_UPGRADE_SCRIPTS"),
    ] = None,
    log_handlers: Annotated[
        list[str] | None,
        typer.Option("--log-handler", envvar="GODOO_RECONCILE_LOG_HANDLERS"),
    ] = None,
    x_sendfile: Annotated[
        bool | None,
        typer.Option("--x-sendfile/--no-x-sendfile", envvar="GODOO_X_SENDFILE"),
    ] = None,
    report_url: Annotated[
        str | None,
        typer.Option(
            "--report-url",
            envvar="GODOO_REPORT_URL",
            help="URL Odoo uses to fetch PDF report assets.",
        ),
    ] = None,
    after_bootstrap_dirs: Annotated[
        list[Path] | None,
        typer.Option("--after-bootstrap-dir", envvar="GODOO_AFTER_BOOTSTRAP_DIRS"),
    ] = None,
    after_restore_dirs: Annotated[
        list[Path] | None,
        typer.Option("--after-restore-dir", envvar="GODOO_AFTER_RESTORE_DIRS"),
    ] = None,
    after_reconcile_dirs: Annotated[
        list[Path] | None,
        typer.Option("--after-reconcile-dir", envvar="GODOO_AFTER_RECONCILE_DIRS"),
    ] = None,
    expected_odoo_major: Annotated[
        int | None,
        typer.Option(
            "--expected-odoo-major",
            envvar="GODOO_EXPECTED_ODOO_MAJOR",
            help="Require the configured Odoo runtime to match this major version.",
        ),
    ] = None,
    install_base_modules: Annotated[
        bool,
        typer.Option(
            envvar="GODOO_INSTALL_BASE_MODULES",
            help="Install base/web when bootstrapping.",
        ),
    ] = True,
    install_workspace_modules: Annotated[bool, CLI.odoo_launch.install_workspace_modules] = True,
    odoo_demo: Annotated[
        bool,
        typer.Option(
            "--odoo-demo/--no-odoo-demo",
            envvar="GODOO_RUNTIME_DEMO",
            help="Load demo data when bootstrapping.",
        ),
    ] = False,
    extra_bootstrap_args: Annotated[list[str] | None, CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
    nginx_config: Annotated[Path | None, CLI.odoo_paths.nginx_config] = None,
) -> int:
    """One-shot init: seed or bootstrap, reconcile, run phase hooks, then exit."""
    if odoo_demo and dump_path is not None:
        message = "--dump-path cannot be combined with demo data."
        raise typer.BadParameter(message, param_hint="--odoo-demo")
    try:
        dataset = resolve_runtime_dataset(
            dataset_root=dump_path,
            seed=seed,
            original_filestore=original_filestore,
            migration_requested=migration_dir is not None,
        )
    except (RuntimeError, OSError, ValueError) as error:
        raise typer.BadParameter(str(error), param_hint="--dump-path") from error
    if dataset is not None:
        seed = dataset.source
        original_filestore = dataset.original_filestore

    migration = None
    if migration_dir is not None:
        _validate_migration_overrides(
            update_modules,
            install_modules,
            pre_upgrade_scripts,
            after_bootstrap_dirs,
            after_restore_dirs,
        )
        if odoo_demo:
            message = "--migration-dir cannot be combined with demo data."
            raise typer.BadParameter(message, param_hint="--odoo-demo")
        if seed is None:
            message = "--migration-dir requires a seed ZIP archive or --dump-path."
            raise typer.BadParameter(message, param_hint="--seed")
        if original_filestore is None:
            message = "--migration-dir requires a nonempty original filestore directory."
            raise typer.BadParameter(message, param_hint="--original-filestore")
        try:
            filestore_nonempty = original_filestore.is_dir() and any(
                path.is_file() for path in original_filestore.rglob("*")
            )
        except OSError as error:
            raise typer.BadParameter(str(error), param_hint="--original-filestore") from error
        if not filestore_nonempty:
            message = f"Original filestore directory is missing or empty: {original_filestore}"
            raise typer.BadParameter(message, param_hint="--original-filestore")
        try:
            migration = resolve_migration_directory(
                migration_dir,
                workspace_root=Path.cwd(),
                additional_upgrade_paths=upgrade_path,
            )
        except (OSError, ValueError) as error:
            raise typer.BadParameter(str(error), param_hint="--migration-dir") from error
        if expected_odoo_major is not None and expected_odoo_major != migration.target_major:
            message = f"Migration transition {migration.directory.name} requires Odoo {migration.target_major}.x."
            raise typer.BadParameter(message, param_hint="--expected-odoo-major")
        expected_odoo_major = migration.target_major
        update_modules = list(migration.update_modules)
        install_modules = list(migration.install_modules)
        pre_upgrade_scripts = [migration.pre_upgrade_script] if update_modules else None
        upgrade_path = list(migration.upgrade_paths) if update_modules else None
        after_reconcile_dirs = [
            migration.after_reconcile_dir,
            *(after_reconcile_dirs or []),
        ]
    if report_url is not None:
        report_url = report_url.strip()
    config = resolve_command_config(
        odoo_main_path=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        odoo_conf_path=odoo_conf_path,
        data_dir=data_dir,
        db_filter=db_filter,
        db_name=db_name,
        db_user=db_user,
        db_host=db_host,
        db_port=db_port,
        db_password=db_password,
        extra={
            "multithread_worker_count": multithread_worker_count,
            "languages": languages,
        },
    )
    try:
        runtime_version = require_odoo_version(config.odoo_install_folder, SUPPORTED_ODOO_VERSION_SPECIFIER)
    except OdooVersionError as error:
        raise typer.BadParameter(str(error), param_hint="--odoo-main-path") from error
    if expected_odoo_major is not None and runtime_version.major != expected_odoo_major:
        message = (
            f"Configured Odoo runtime is {runtime_version.raw}; "
            f"the selected transition requires Odoo {expected_odoo_major}.x."
        )
        raise typer.BadParameter(message, param_hint="--expected-odoo-major")
    _validate_pre_upgrade_options(seed, pre_upgrade_scripts, update_modules, after_restore_dirs)
    validated_archive = None
    if x_sendfile_enabled(config, x_sendfile) and not report_url:
        message = "Set GODOO_REPORT_URL when X-Sendfile is enabled."
        raise typer.BadParameter(message, param_hint="--report-url")
    runtime_seed = seed
    validated_archive = None
    if dataset is None or dataset.kind == "archive":
        try:
            validated_archive = prepare_archive_with_original_filestore(runtime_seed, original_filestore)
        except (OSError, ValueError) as error:
            raise typer.BadParameter(str(error), param_hint="--original-filestore") from error

    def already_prepared(_conf: GodooConfig) -> None:
        """Skip preparation already completed by init."""
        return None

    def prepare(conf: GodooConfig) -> None:
        """Prepare Odoo and the optional Nginx static-file lookup."""
        if nginx_config is not None:
            write_nginx_static_config(conf, nginx_config, runtime_version)
        prepare_runtime(conf, x_sendfile=x_sendfile)

    def ensure(conf: GodooConfig) -> bool:
        """Bootstrap the runtime without repeating preparation."""
        return ensure_runtime(
            conf,
            preparer=already_prepared,
            odoo_demo=odoo_demo,
            extra_bootstrap_args=extra_bootstrap_args,
            install_workspace_modules=install_workspace_modules,
            install_base_modules=install_base_modules,
            allow_lifecycle_retry=True,
        )

    def seed_runtime(conf: GodooConfig) -> None:
        """Load the selected runtime seed."""
        assert runtime_seed is not None
        if dataset is not None and dataset.kind == "custom":
            assert dataset.original_filestore is not None
            restore_custom_runtime(
                connection=conf.db_connection,
                db_template="template0",
                dump_path=runtime_seed,
                filestore_source=dataset.original_filestore,
                data_dir=conf.data_dir,
                force=True,
                lifecycle_owner="init",
                target_major=runtime_version.major,
                expected_dump_identity=dataset.dump_identity,
                keep_reset_baseline=keep_reset_baseline,
                restore_jobs=restore_jobs,
            )
            return
        result = load_runtime_archive(
            archive_path=runtime_seed,
            odoo_bin_path=conf.odoo_install_folder / "odoo-bin",
            odoo_conf_path=conf.odoo_conf_path,
            data_dir=conf.data_dir,
            force=True,
            connection=conf.db_connection,
            original_filestore=original_filestore,
            _validated_archive=validated_archive,
            lifecycle_owner="init",
            require_same_archive_identity=True,
            keep_reset_baseline=keep_reset_baseline,
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
            preparer=prepare,
            preflight=lambda conf: preflight_reconcile_dependencies(
                conf,
                update_modules,
                install_modules,
                ignore_missing_installed_modules=bool(pre_upgrade_scripts),
            ),
            post_restore_preflight=lambda conf: preflight_reconcile_dependencies(
                conf,
                update_modules,
                install_modules,
                ignore_missing_installed_modules=bool(pre_upgrade_scripts),
            ),
            reconciler=lambda conf: reconcile_runtime(
                conf,
                preparer=already_prepared,
                allow_lifecycle_retry=True,
                reconciler=lambda runtime: reconcile_modules(
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
            after_reconcile_dirs=after_reconcile_dirs,
            hook_runner=run_lifecycle_hook,
            keep_reset_baseline=keep_reset_baseline,
        )
        if result == 0 and report_url:
            set_report_url(config, report_url)
    except LifecycleBootstrapError as error:
        LOGGER.exception("Native Odoo bootstrap failed during deployment initialization")
        return CLI.returner(error.return_code)
    except (ValueError, RuntimeError, OSError, psycopg2.Error):
        LOGGER.exception("Deployment initialization failed")
        return CLI.returner(1)
    LOGGER.info("Runtime initialization outcome: %s", outcome.value)
    return CLI.returner(result)
