import logging
import re
from pathlib import Path
from typing import List

import typer
from typing_extensions import Annotated

from ...cli_common import CommonCLI
from ...helpers.odoo_files import (
    get_addon_paths,
    get_changed_modules_and_depends,
    get_depends_of_module,
    get_odoo_module_paths,
)
from ...helpers.system import run_cmd
from ..db.connection import DBConnection
from ..launch import pre_launch
from ..shell import odoo_pregenerate_assets

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)


def _test_modules_special_cases(in_modules: List[str], workspace_addon_path: Path):
    if len(in_modules) == 1:
        # In _modules could be a command
        out_modules = []
        command = in_modules[0]
        if command == "all":
            out_modules = get_odoo_module_paths(workspace_addon_path)
        elif re_match := re.match(r"changes\:(.*)", command):
            compare_branch = re_match.group(1)
            changed_modules = get_changed_modules_and_depends(
                diff_ref=compare_branch,
                addon_path=workspace_addon_path,
            )
            out_modules = changed_modules
        else:
            return in_modules
        return [p.stem for p in out_modules]
    return in_modules


@CLI.arg_annotator
def odoo_get_changed_modules(
    diff_ref: str = typer.Argument(..., help="Git Ref/Branch to compare against"),
    workspace_addon_path=CLI.odoo_paths.workspace_addon_path,
):
    """Get Modules that have changed compared to diff_ref"""
    changed_modules = get_changed_modules_and_depends(diff_ref=diff_ref, addon_path=workspace_addon_path)
    if not changed_modules:
        return
    print("\n".join(sorted([p.stem for p in changed_modules])))  # pylint: disable=print-used


@CLI.arg_annotator
def odoo_run_tests(
    test_modules: List[str] = typer.Argument(
        ...,
        help="Modules to install and test (Use 'all' for all Workspace modules), ('changes:<branch> to compare git changes)",
    ),
    odoo_main_path=CLI.odoo_paths.bin_path,
    workspace_addon_path=CLI.odoo_paths.workspace_addon_path,
    thirdparty_addon_path=CLI.odoo_paths.thirdparty_addon_path,
    odoo_conf_path=CLI.odoo_paths.conf_path,
    extra_launch_args=CLI.odoo_launch.extra_cmd_args,
    extra_bootstrap_args=CLI.odoo_launch.extra_cmd_args,
    languages=CLI.odoo_launch.languages,
    db_filter=CLI.database.db_filter,
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
    skip_test_modules: Annotated[
        List[str],
        typer.Option(envvar="ODOO_TEST_SKIP_MODULES", help="Modules not to Test even if specified in test_modules"),
    ] = None,
    odoo_log_level: str = typer.Option("test", help="Log level"),
    pregenerate_assets: Annotated[bool, typer.Option(help="Pregenerate assets before running tests")] = True,
):
    """Bootstrap or Launch odoo in Testing Mode. Exits after Run, so no webserver is started. (Will set weird odoo.conf if it needs to bootstrap)"""

    test_modules = _test_modules_special_cases(test_modules, workspace_addon_path)
    addon_paths = get_addon_paths(odoo_main_path, workspace_addon_path, thirdparty_addon_path)
    all_modules = get_odoo_module_paths(addon_paths)
    depends = []
    for module in test_modules:
        module_path = next((p for p in all_modules if p.stem == module), None)
        depends += get_depends_of_module(all_modules, module_path)
    depends = list(set(depends))

    if skip_test_modules:
        skip_test_modules = [
            m for m in skip_test_modules if m in test_modules
        ]  # Filter out skip mods that arent requested
        if skip_test_modules:
            LOGGER.info("Skipping Tests for Modules:\n%s", skip_test_modules)
            test_modules = [m for m in test_modules if m not in skip_test_modules]

    if not test_modules:
        LOGGER.info("Nothing to Test. Skipping.")
        return

    test_module_list = ",".join(["/" + m for m in test_modules])
    module_list = ",".join(test_modules)

    LOGGER.info("Testing Odoo Modules:\n%s", sorted(test_modules))
    if "account" in [p.stem for p in depends]:
        bootstrap_args = [f"--init {module_list},l10n_generic_coa"]
    else:
        bootstrap_args = [f"--init {module_list}"]
    bootstrap_args.append(f"--log-level {odoo_log_level}")

    launch_args = [
        f"-u {module_list}",
        f"--log-level {odoo_log_level}",
        f"--test-tags {test_module_list}",
        "--stop-after-init",
    ]

    if extra_launch_args:
        launch_args = extra_launch_args + launch_args

    launch_or_bootstrap = False
    if not pregenerate_assets:
        # If we dont pregenerate assets, we can run the Tests directly in Bootstrap
        # This saves one Upgrade iteration
        launch_or_bootstrap = True
        bootstrap_args.append(f"--test-tags {test_module_list}")

    bootstrap_args = extra_bootstrap_args + bootstrap_args

    db_connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name=db_name,
    )

    launch_cmd = pre_launch(
        odoo_main_path=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
        odoo_conf_path=odoo_conf_path,
        db_filter=db_filter,
        db_connection=db_connection,
        dev_mode=False,
        install_workspace_addons=False,
        extra_launch_args=launch_args,
        extra_bootstrap_args=bootstrap_args,
        multithread_worker_count=0,
        odoo_demo=False,
        languages=languages,
        launch_or_bootstrap=launch_or_bootstrap,
    )
    if isinstance(launch_cmd, str):
        if pregenerate_assets:
            odoo_pregenerate_assets(odoo_main_path)
        LOGGER.info("Launching Odoo Tests")
        return CLI.returner(run_cmd(launch_cmd).returncode)

    return CLI.returner(launch_cmd)
