"""Test run command implementations for the test CLI group."""

import logging
import re
from pathlib import Path
from typing import Annotated

import typer

from ...helpers.modules_git import get_changed_modules_and_depends
from ...models import GodooModule, GodooModules
from ...runtime.odoo import bootstrap_and_prep_launch_cmd, run_odoo_command
from ..common import CommonCLI
from ..configuration import require_cli_odoo_version, resolve_command_config

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)


def _resolve_test_module_names(in_modules: list[str], workspace_addon_path: Path):
    if len(in_modules) == 1:
        # In _modules could be a command
        out_modules = []
        command = in_modules[0]
        if command == "all":
            out_modules = GodooModules(workspace_addon_path).get_modules()
        elif re_match := re.match(r"changes\:(.*)", command):
            compare_branch = re_match.group(1)
            changed_modules = get_changed_modules_and_depends(
                diff_ref=compare_branch,
                addon_path=workspace_addon_path,
            )
            out_modules = changed_modules
        else:
            return in_modules
        return [p.name for p in out_modules]
    return in_modules


def odoo_get_changed_modules(
    diff_ref: Annotated[str, typer.Argument(help="Git Ref/Branch to compare against")],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
):
    """Return modules changed from the selected Git ref."""
    changed_modules = get_changed_modules_and_depends(diff_ref=diff_ref, addon_path=workspace_addon_path)
    if not changed_modules:
        return
    print("\n".join(sorted([p.name for p in changed_modules])))  # pylint: disable=print-used


def _test_modules_to_run(
    module_reg: GodooModules,
    test_module_names: list[str],
    skip_test_modules: list[str] | None,
) -> tuple[list[str], list[GodooModule]]:
    """Resolve test modules and their dependencies, omitting explicit skips."""
    test_modules = list(module_reg.get_modules(test_module_names))
    dependencies: list[GodooModule] = []
    for module in test_modules:
        dependencies.extend(module_reg.get_module_dependencies(module))
    dependencies = list(set(dependencies))

    if skip_test_modules:
        skipped = [module for module in skip_test_modules if module in test_module_names]
        if skipped:
            LOGGER.info("Skipping Tests for Modules:\n%s", skipped)
            test_modules = [module for module in test_modules if module.name not in skipped]

    return [module.name for module in test_modules], dependencies


def odoo_run_tests(
    test_module_names: Annotated[
        list[str],
        typer.Argument(
            help="""
        Space separated list of Modules to Test or special commands:

         'all' for all modules in `workspace_addon_path`

         'changes:<ref>' detect modules by changed files compared to <ref> (git diff)
        """,
        ),
    ],
    odoo_main_path: Annotated[Path, CLI.odoo_paths.bin_path],
    workspace_addon_path: Annotated[Path, CLI.odoo_paths.workspace_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_filter: Annotated[str, CLI.database.db_filter],
    db_user: Annotated[str, CLI.database.db_user],
    db_name: Annotated[str, CLI.database.db_name],
    data_dir: Annotated[Path | None, CLI.odoo_paths.data_dir] = None,
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    odoo_log_level: Annotated[str, typer.Option(help="Log level")] = "test",
    extra_launch_args: Annotated[list[str] | None, CLI.odoo_launch.extra_cmd_args] = None,
    extra_bootstrap_args: Annotated[list[str] | None, CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
    languages: Annotated[str, CLI.odoo_launch.languages] = "de_DE,en_US",
    test_tags: Annotated[
        str | None, typer.Option("--test-tags", envvar="ODOO_TEST_TAGS", help="Additional Odoo test tags")
    ] = None,
    test_file: Annotated[
        Path | None, typer.Option("--test-file", envvar="ODOO_TEST_FILE", help="Odoo test file to run")
    ] = None,
    skip_test_modules: Annotated[
        list[str] | None,
        typer.Option(
            envvar="ODOO_TEST_SKIP_MODULES",
            help="Modules not to Test even if specified in test_modules",
        ),
    ] = None,
):
    """Run Odoo tests to completion without starting the web server."""
    test_module_names = _resolve_test_module_names(test_module_names, workspace_addon_path)
    if not test_module_names:
        LOGGER.info("No Modules to Test. Skipping.")
        return

    godoo_conf = resolve_command_config(
        odoo_main_path=odoo_main_path,
        odoo_conf_path=odoo_conf_path,
        workspace_addon_path=workspace_addon_path,
        data_dir=data_dir,
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_filter=db_filter,
        extra={"multithread_worker_count": 0, "languages": languages},
    )
    odoo_version = require_cli_odoo_version(godoo_conf.odoo_install_folder, ">=19")

    # Resolve the module graph before deciding whether there is a runnable test set.
    module_reg = GodooModules(godoo_conf.addon_paths)
    module_names, depends = _test_modules_to_run(module_reg, test_module_names, skip_test_modules)

    if not module_names:
        LOGGER.info("Nothing to Test. Skipping.")
        return

    test_module_list = ",".join(["/" + m for m in module_names])
    module_list = ",".join(module_names)

    LOGGER.info("Testing Odoo Modules:\n%s", sorted(module_names))
    if "account" in [p.name for p in depends] and odoo_version.major <= 16:
        # l10n_generic_coa got removed in Odoo 16
        bootstrap_args = [f"--init {module_list},l10n_generic_coa"]
    else:
        bootstrap_args = [f"--init {module_list}"]
    bootstrap_args.append(f"--log-level {odoo_log_level}")

    selected_test_tags = ",".join(filter(None, [test_module_list, test_tags or ""]))
    launch_args = [
        f"-u {module_list}",
        f"--log-level {odoo_log_level}",
        f"--test-tags {selected_test_tags}",
        "--stop-after-init",
    ]

    if test_file:
        launch_args.append(f"--test-file {test_file}")
    if extra_launch_args:
        launch_args = extra_launch_args + launch_args

    if extra_bootstrap_args:
        bootstrap_args = extra_bootstrap_args + bootstrap_args

    launch_cmd = bootstrap_and_prep_launch_cmd(
        config=godoo_conf,
        dev_mode=False,
        install_workspace_addons=False,
        extra_launch_args=launch_args,
        extra_bootstrap_args=bootstrap_args,
        odoo_demo=False,
        launch_or_bootstrap=False,
    )
    if isinstance(launch_cmd, list):
        LOGGER.info("Launching Odoo tests on database '%s' using config %s", db_name, odoo_conf_path)
        return CLI.returner(run_odoo_command(launch_cmd).returncode)

    return CLI.returner(launch_cmd)
