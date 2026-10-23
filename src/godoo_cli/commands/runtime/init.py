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
from ...runtime.hooks import discover_hooks, has_eligible_hooks, run_prepare_hooks
from ...runtime.lifecycle import (
    LifecycleBootstrapError,
    deployment_init,
    ensure_runtime,
    preflight_reconcile_dependencies,
    reconcile_modules,
    reconcile_runtime,
    split_lifecycle_values,
    split_upgrade_paths,
)
from ...runtime.locks import read_runtime_lifecycle, runtime_readiness_marker
from ...runtime.nginx import write_nginx_static_config
from ...runtime.odoo import (
    SUPPORTED_ODOO_VERSION_SPECIFIER,
    OdooVersionError,
    prepare_runtime,
    require_odoo_version,
    set_report_url,
    x_sendfile_enabled,
)
from ...runtime.restore import RuntimeRestoreError, restore_custom_runtime
from ..common import CommonCLI
from ..configuration import resolve_command_config

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def _validate_pre_upgrade_options(
    restore_requested: bool,
    pre_upgrade_scripts: list[Path] | None,
    update_modules: list[str] | None,
    after_restore_hooks_selected: bool,
) -> None:
    """Reject hook combinations that would start Odoo before pre-upgrade scripts."""
    if restore_requested and pre_upgrade_scripts and after_restore_hooks_selected:
        message = "An eligible after_restore hook cannot run with --pre-upgrade-script because it starts Odoo first."
        raise typer.BadParameter(message, param_hint="--hooks-dir")
    if pre_upgrade_scripts and not split_lifecycle_values(update_modules):
        message = "--pre-upgrade-script requires at least one --update module."
        raise typer.BadParameter(message, param_hint="--update")


def _validate_native_paths(
    upgrade_path: list[Path] | None, pre_upgrade_scripts: list[Path] | None, update_modules: list[str] | None
) -> tuple[list[Path], list[Path] | None]:
    """Resolve native upgrade inputs before lifecycle state can change."""
    upgrade_paths = split_upgrade_paths(upgrade_path)
    for path in upgrade_paths:
        if not path.is_dir():
            message = f"Native upgrade directory does not exist: {path}"
            raise typer.BadParameter(message, param_hint="--upgrade-path")
    scripts = [path.expanduser().resolve() for path in pre_upgrade_scripts or []]
    for path in scripts:
        if not path.is_file():
            message = f"Native pre-upgrade script does not exist: {path}"
            raise typer.BadParameter(message, param_hint="--pre-upgrade-script")
    if (upgrade_paths or scripts) and not split_lifecycle_values(update_modules):
        message = "Native upgrade paths and pre-upgrade scripts require at least one --update module."
        raise typer.BadParameter(message, param_hint="--update")
    return upgrade_paths, scripts or None


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
    verify_archive_crc: Annotated[bool, CLI.database.verify_archive_crc] = False,
    restore_jobs: Annotated[
        int,
        typer.Option(
            "--restore-jobs",
            envvar="GODOO_RESTORE_JOBS",
            min=1,
            help="Parallel pg_restore jobs for custom-format dataset imports.",
        ),
    ] = 1,
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
    hooks_dir: Annotated[
        list[Path] | None,
        typer.Option(
            "--hooks-dir",
            envvar="GODOO_HOOK_DIRS",
            help="Directory containing lifecycle hook declarations; repeat to preserve ordering.",
        ),
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
        )
    except (RuntimeError, OSError, ValueError) as error:
        raise typer.BadParameter(str(error), param_hint="--dump-path") from error
    if dataset is not None:
        seed = dataset.source
        original_filestore = dataset.original_filestore

    try:
        hooks = discover_hooks(hooks_dir or [])
        prepared_modules = run_prepare_hooks(hooks, environment=os.environ.get("GODOO_ENVIRONMENT") or None)
    except (OSError, TypeError, ValueError) as error:
        raise typer.BadParameter(str(error), param_hint="--hooks-dir") from error
    update_modules = list(dict.fromkeys([*split_lifecycle_values(update_modules), *prepared_modules.update])) or None
    install_modules = list(dict.fromkeys([*split_lifecycle_values(install_modules), *prepared_modules.install])) or None
    upgrade_path, pre_upgrade_scripts = _validate_native_paths(upgrade_path, pre_upgrade_scripts, update_modules)
    environment = os.environ.get("GODOO_ENVIRONMENT") or None
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
            f"the selected runtime requires Odoo {expected_odoo_major}.x."
        )
        raise typer.BadParameter(message, param_hint="--expected-odoo-major")
    persisted = read_runtime_lifecycle(runtime_readiness_marker(config.data_dir, config.db_name), config.db_name)
    _validate_pre_upgrade_options(
        seed is not None or (persisted is not None and persisted["outcome"] == "restored"),
        pre_upgrade_scripts,
        update_modules,
        has_eligible_hooks(hooks, phase="after_restore", outcome="restored", environment=environment),
    )
    if x_sendfile_enabled(config, x_sendfile) and not report_url:
        message = "Set GODOO_REPORT_URL when X-Sendfile is enabled."
        raise typer.BadParameter(message, param_hint="--report-url")
    runtime_seed = seed
    validated_archive = None

    def prepare_seed(_conf: GodooConfig) -> None:
        """Validate restore inputs before creating initialization recovery state."""
        nonlocal validated_archive
        if dataset is not None and dataset.kind == "custom":
            return
        try:
            validated_archive = prepare_archive_with_original_filestore(
                runtime_seed,
                original_filestore,
                verify_archive_crc=verify_archive_crc,
            )
        except (OSError, ValueError, RuntimeRestoreError) as error:
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
            verify_archive_crc=verify_archive_crc,
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
            seed_preflight=prepare_seed,
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
            hooks=hooks,
            environment=environment,
            upgrade_path=upgrade_path,
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
    finally:
        if validated_archive is not None:
            validated_archive.cleanup()
    LOGGER.info("Runtime initialization outcome: %s", outcome.value)
    return CLI.returner(result)
