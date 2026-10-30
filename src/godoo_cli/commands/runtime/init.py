"""High-level, testable gOdoo runtime lifecycle commands."""

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import psycopg2
import typer

from ...models import GodooConfig
from ...runtime.archive import load_runtime_archive, prepare_archive_with_original_filestore
from ...runtime.checksums import ModulePlan, module_states, pending_installs, plan_module_changes
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
from ...runtime.status import inspect_runtime
from ..common import CommonCLI
from ..configuration import resolve_command_config

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()


@dataclass(frozen=True, kw_only=True)
class InitOptions:
    """Describe one initialization policy separately from its resolved runtime target."""

    seed: Path | None = None
    dump_path: Path | None = None
    if_needed: bool = False
    keep_reset_baseline: bool = False
    verify_archive_crc: bool = False
    restore_jobs: int = 1
    original_filestore: Path | None = None
    update_modules: list[str] | None = None
    install_modules: list[str] | None = None
    update_changed: bool = False
    upgrade_path: list[Path] | None = None
    pre_upgrade_scripts: list[Path] | None = None
    log_handlers: list[str] | None = None
    x_sendfile: bool | None = None
    report_url: str | None = None
    hooks_dir: list[Path] | None = None
    expected_odoo_major: int | None = None
    install_base_modules: bool = True
    install_workspace_modules: bool = True
    odoo_demo: bool = False
    extra_bootstrap_args: list[str] | None = None
    nginx_config: Path | None = None


def _validate_pre_upgrade_options(
    restore_requested: bool,
    pre_upgrade_scripts: list[Path] | None,
    update_modules: list[str] | None,
    after_restore_hooks_selected: bool,
) -> None:
    """Reject hook combinations that would start Odoo before pre-upgrade scripts."""
    if restore_requested and pre_upgrade_scripts and update_modules and after_restore_hooks_selected:
        message = "An eligible after_restore hook cannot run with --pre-upgrade-script because it starts Odoo first."
        raise typer.BadParameter(message, param_hint="--hooks-dir")


def _validate_native_paths(
    upgrade_path: list[Path] | None, pre_upgrade_scripts: list[Path] | None
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
    return upgrade_paths, scripts or None


# Keep validation and execution ordering visible at this public command boundary.
# Typer declares each public CLI option in this callback signature.
def deployment_init_odoo_runtime(  # pylint: disable=too-many-arguments
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
    if_needed: Annotated[
        bool,
        typer.Option(
            "--if-needed/--no-if-needed",
            envvar="GODOO_INIT_IF_EMPTY",
            help="Skip a ready runtime; initialize missing/empty state or resume incomplete initialization.",
        ),
    ] = False,
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
    update_changed: Annotated[
        bool,
        typer.Option(
            "--update-changed/--no-update-changed",
            envvar="GODOO_UPDATE_CHANGED",
            help="Select native upgrades from a database-backed source checksum baseline.",
        ),
    ] = False,
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
    """Initialize and reconcile once, optionally skipping a ready runtime."""

    def resolve_config() -> GodooConfig:
        """Resolve the same runtime target for readiness and initialization."""
        return resolve_command_config(
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

    return CLI.returner(
        initialize_odoo_runtime(
            resolve_config,
            InitOptions(
                seed=seed,
                dump_path=dump_path,
                if_needed=if_needed,
                keep_reset_baseline=keep_reset_baseline,
                verify_archive_crc=verify_archive_crc,
                restore_jobs=restore_jobs,
                original_filestore=original_filestore,
                update_modules=update_modules,
                install_modules=install_modules,
                update_changed=update_changed,
                upgrade_path=upgrade_path,
                pre_upgrade_scripts=pre_upgrade_scripts,
                log_handlers=log_handlers,
                x_sendfile=x_sendfile,
                report_url=report_url,
                hooks_dir=hooks_dir,
                expected_odoo_major=expected_odoo_major,
                install_base_modules=install_base_modules,
                install_workspace_modules=install_workspace_modules,
                odoo_demo=odoo_demo,
                extra_bootstrap_args=extra_bootstrap_args,
                nginx_config=nginx_config,
            ),
        )
    )


# Keep initialization validation and mutation ordering together.
def initialize_odoo_runtime(resolve_config: Callable[[], GodooConfig], request: InitOptions) -> int:  # noqa: C901
    """Validate declarations, resolve one target, and initialize without exiting the CLI."""
    dump_path = request.dump_path
    install_modules = request.install_modules
    original_filestore = request.original_filestore
    pre_upgrade_scripts = request.pre_upgrade_scripts
    report_url = request.report_url
    seed = request.seed
    update_modules = request.update_modules
    upgrade_path = request.upgrade_path
    config = None
    if request.if_needed:
        config = resolve_config()
        payload, status = inspect_runtime(
            config.db_connection.with_db(config.db_name, readonly=True),
            config.data_dir,
            Path("/odoo/godoo-source-provenance.json"),
        )
        typer.echo(json.dumps(payload, sort_keys=True))
        if status not in (20, 21, 23):
            return status
    if request.odoo_demo:
        if seed is not None:
            message = "--seed cannot be combined with demo data."
            raise typer.BadParameter(message, param_hint="--odoo-demo")
        dump_path = original_filestore = None
    try:
        dataset = resolve_runtime_dataset(dataset_root=dump_path, seed=seed, original_filestore=original_filestore)
    except (RuntimeError, OSError, ValueError) as error:
        raise typer.BadParameter(str(error), param_hint="--dump-path") from error
    if dataset is not None:
        seed = dataset.source
        original_filestore = dataset.original_filestore
    try:
        hooks = discover_hooks(request.hooks_dir or [])
        prepared_modules = run_prepare_hooks(hooks, environment=os.environ.get("GODOO_ENVIRONMENT") or None)
    except (OSError, TypeError, ValueError) as error:
        raise typer.BadParameter(str(error), param_hint="--hooks-dir") from error
    if request.update_changed and any(
        (
            has_eligible_hooks(
                hooks, phase=phase, outcome=outcome, environment=os.environ.get("GODOO_ENVIRONMENT") or None
            )
            for phase, outcome in (("after_restore", "restored"), ("after_bootstrap", "bootstrapped"))
        )
    ):
        message = "--update-changed requires database hooks to use after_reconcile."
        raise typer.BadParameter(message, param_hint="--hooks-dir")
    update_modules = list(dict.fromkeys([*split_lifecycle_values(update_modules), *prepared_modules.update])) or None
    install_modules = list(dict.fromkeys([*split_lifecycle_values(install_modules), *prepared_modules.install])) or None
    upgrade_path, pre_upgrade_scripts = _validate_native_paths(upgrade_path, pre_upgrade_scripts)
    environment = os.environ.get("GODOO_ENVIRONMENT") or None
    if report_url is not None:
        report_url = report_url.strip()
    config = config or resolve_config()
    try:
        runtime_version = require_odoo_version(config.odoo_install_folder, SUPPORTED_ODOO_VERSION_SPECIFIER)
    except OdooVersionError as error:
        raise typer.BadParameter(str(error), param_hint="--odoo-main-path") from error
    if request.expected_odoo_major is not None and runtime_version.major != request.expected_odoo_major:
        message = f"Configured Odoo runtime is {runtime_version.raw}; the selected runtime requires Odoo {request.expected_odoo_major}.x."
        raise typer.BadParameter(message, param_hint="--expected-odoo-major")
    persisted = read_runtime_lifecycle(runtime_readiness_marker(config.data_dir, config.db_name), config.db_name)
    _validate_pre_upgrade_options(
        seed is not None or (persisted is not None and persisted["outcome"] == "restored"),
        pre_upgrade_scripts,
        update_modules,
        has_eligible_hooks(hooks, phase="after_restore", outcome="restored", environment=environment),
    )
    if x_sendfile_enabled(config, request.x_sendfile) and (not report_url):
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
                runtime_seed, original_filestore, verify_archive_crc=request.verify_archive_crc
            )
        except (OSError, ValueError, RuntimeRestoreError) as error:
            raise typer.BadParameter(str(error), param_hint="--original-filestore") from error

    def already_prepared(_conf: GodooConfig) -> None:
        """Skip preparation already completed by init."""
        return None

    def prepare(conf: GodooConfig) -> None:
        """Prepare Odoo and the optional Nginx static-file lookup."""
        if request.nginx_config is not None:
            write_nginx_static_config(conf, request.nginx_config, runtime_version)
        prepare_runtime(conf, x_sendfile=request.x_sendfile)

    def ensure(conf: GodooConfig) -> bool:
        """Bootstrap the runtime without repeating preparation."""
        return ensure_runtime(
            conf,
            preparer=already_prepared,
            odoo_demo=request.odoo_demo,
            extra_bootstrap_args=request.extra_bootstrap_args,
            install_workspace_modules=request.install_workspace_modules,
            install_base_modules=request.install_base_modules,
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
                keep_reset_baseline=request.keep_reset_baseline,
                restore_jobs=request.restore_jobs,
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
            keep_reset_baseline=request.keep_reset_baseline,
            verify_archive_crc=request.verify_archive_crc,
        )
        if result:
            message = f"Odoo seed archive load failed for runtime '{conf.db_name}' (exit code {result})"
            raise RuntimeError(message)

    plan: ModulePlan | None = None
    selected_updates = split_lifecycle_values(update_modules)
    selected_installs = split_lifecycle_values(install_modules)

    def plan_reconciliation(conf: GodooConfig) -> None:
        """Select native work under the lifecycle lock before any database hook."""
        nonlocal plan, selected_updates, selected_installs
        if request.update_changed:
            plan = plan_module_changes(
                conf, selected_updates, selected_installs, pre_upgrade_scripts=bool(pre_upgrade_scripts)
            )
            selected_updates, selected_installs = (plan.updates, plan.installs)
        elif selected_installs:
            selected_installs = pending_installs(module_states(conf), selected_installs)

    def reconcile(conf: GodooConfig) -> int:
        """Run native reconciliation and checkpoint only verified source state."""
        result = reconcile_runtime(
            conf,
            preparer=already_prepared,
            allow_lifecycle_retry=True,
            reconciler=lambda runtime: reconcile_modules(
                runtime,
                selected_updates,
                selected_installs,
                upgrade_path=upgrade_path,
                pre_upgrade_scripts=pre_upgrade_scripts,
                log_handlers=request.log_handlers,
            ),
        )
        if result == 0 and plan is not None:
            plan.finish(conf)
        return result

    try:
        outcome, result = deployment_init(
            config,
            seed_requested=runtime_seed is not None,
            seeder=seed_runtime,
            ensure=ensure,
            preparer=prepare,
            preflight=lambda conf: preflight_reconcile_dependencies(
                conf, update_modules, install_modules, ignore_missing_installed_modules=bool(pre_upgrade_scripts)
            ),
            seed_preflight=prepare_seed,
            post_restore_preflight=lambda conf: preflight_reconcile_dependencies(
                conf, update_modules, install_modules, ignore_missing_installed_modules=bool(pre_upgrade_scripts)
            ),
            reconcile_preflight=plan_reconciliation,
            reconciler=reconcile,
            hooks=hooks,
            environment=environment,
            upgrade_path=upgrade_path,
            keep_reset_baseline=request.keep_reset_baseline,
        )
        if result == 0 and report_url:
            set_report_url(config, report_url)
    except LifecycleBootstrapError as error:
        LOGGER.exception("Native Odoo bootstrap failed during deployment initialization")
        return error.return_code
    except (ValueError, RuntimeError, OSError, psycopg2.Error):
        LOGGER.exception("Deployment initialization failed")
        return 1
    finally:
        if validated_archive is not None:
            validated_archive.cleanup()
    LOGGER.info("Runtime initialization outcome: %s", outcome.value)
    return result
