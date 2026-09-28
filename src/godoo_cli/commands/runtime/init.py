"""High-level, testable gOdoo runtime lifecycle commands."""

import logging
from pathlib import Path
from typing import Annotated

import psycopg2
import typer

from ...models import GodooConfig
from ...runtime.archive import load_runtime_archive
from ...runtime.lifecycle import (
    LifecycleBootstrapError,
    deployment_init,
    ensure_runtime,
    preflight_reconcile_dependencies,
    reconcile_modules,
    reconcile_runtime,
    run_lifecycle_hook,
)
from ...runtime.odoo import (
    prepare_runtime,
    set_report_url,
    x_sendfile_enabled,
)
from ..common import CommonCLI
from ..configuration import resolve_command_config

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


def deployment_init_odoo_runtime(
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
    update_modules: Annotated[list[str] | None, typer.Option("--update", envvar="GODOO_RECONCILE_UPDATE")] = None,
    install_modules: Annotated[list[str] | None, typer.Option("--install", envvar="GODOO_RECONCILE_INSTALL")] = None,
    upgrade_path: Annotated[Path | None, typer.Option("--upgrade-path", envvar="GODOO_RECONCILE_UPGRADE_PATH")] = None,
    pre_upgrade_scripts: Annotated[
        list[Path] | None,
        typer.Option("--pre-upgrade-script", envvar="GODOO_RECONCILE_PRE_UPGRADE_SCRIPTS"),
    ] = None,
    log_handlers: Annotated[
        list[str] | None, typer.Option("--log-handler", envvar="GODOO_RECONCILE_LOG_HANDLERS")
    ] = None,
    x_sendfile: Annotated[bool | None, typer.Option("--x-sendfile/--no-x-sendfile", envvar="GODOO_X_SENDFILE")] = None,
    report_url: Annotated[
        str | None,
        typer.Option("--report-url", envvar="GODOO_REPORT_URL", help="URL Odoo uses to fetch PDF report assets."),
    ] = None,
    after_bootstrap_dirs: Annotated[
        list[Path] | None, typer.Option("--after-bootstrap-dir", envvar="GODOO_AFTER_BOOTSTRAP_DIRS")
    ] = None,
    after_restore_dirs: Annotated[
        list[Path] | None, typer.Option("--after-restore-dir", envvar="GODOO_AFTER_RESTORE_DIRS")
    ] = None,
    after_reconcile_dirs: Annotated[
        list[Path] | None, typer.Option("--after-reconcile-dir", envvar="GODOO_AFTER_RECONCILE_DIRS")
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
    extra_bootstrap_args: Annotated[list[str] | None, CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
) -> int:
    """One-shot init: seed or bootstrap, reconcile, run phase hooks, then exit."""
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
        extra={"multithread_worker_count": multithread_worker_count, "languages": languages},
    )
    if x_sendfile_enabled(config, x_sendfile) and not report_url:
        message = "Set GODOO_REPORT_URL when X-Sendfile is enabled."
        raise typer.BadParameter(message, param_hint="--report-url")
    runtime_seed = seed

    def already_prepared(_conf: GodooConfig) -> None:
        """Skip preparation already completed by init."""
        return None

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
        """Load the selected native seed archive."""
        assert runtime_seed is not None
        result = load_runtime_archive(
            db_name=conf.db_name,
            archive_path=runtime_seed,
            odoo_bin_path=conf.odoo_install_folder / "odoo-bin",
            odoo_conf_path=conf.odoo_conf_path,
            data_dir=conf.data_dir,
            force=True,
            connection=conf.db_connection,
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
            preparer=lambda conf: prepare_runtime(conf, x_sendfile=x_sendfile),
            preflight=lambda conf: preflight_reconcile_dependencies(conf, update_modules, install_modules),
            post_restore_preflight=lambda conf: preflight_reconcile_dependencies(conf, update_modules, install_modules),
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
