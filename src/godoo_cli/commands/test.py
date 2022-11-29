import logging
import re
from pathlib import Path
from typing import List

import typer

from ..helpers.cli import typer_retuner
from ..helpers.odoo_files import get_changed_modules, get_depends_of_module, get_odoo_module_paths
from .launch import launch_odoo as launch_odoo

LOGGER = logging.getLogger(__name__)


def _test_modules_special_cases(in_modules: List[str], workspace_addon_path: Path):
    if len(in_modules) == 1:
        command = in_modules[0]
        out_modules = []
        if command == "all":
            out_modules = get_odoo_module_paths(workspace_addon_path)

        if re_match := re.match(r"changes\:(.*)", command):
            compare_branch = re_match.group(1)
            changed_modules = get_changed_modules(addon_path=workspace_addon_path, diff_branch=compare_branch)
            if not changed_modules:
                return []
            change_modules_depends = []
            for module in changed_modules:
                change_modules_depends += get_depends_of_module(
                    out_modules, module, already_done_modules=change_modules_depends
                )
            out_modules = changed_modules + change_modules_depends
        if out_modules:
            out_modules_with_tests = [p for p in out_modules if any(p.rglob("tests/__init__.py"))]
            return [p.stem for p in out_modules_with_tests]
    return in_modules


def odoo_test(
    ctx: typer.Context,
    test_modules: List[str] = typer.Argument(
        ...,
        help="Modules to install and test (Use 'all' for all Workspace modules), ('changes:<branch> to compare git changes)",
    ),
    skip_test_modules: List[str] = typer.Option(
        [], envvar="ODOO_TEST_SKIP_MODULES", help="Modules not to Test even if specified in test_modules"
    ),
    odoo_log_level: str = typer.Option("test", help="Log level"),
):
    """Bootstrap or Launch odoo in Testing Mode."""
    workspace_addon_path = ctx.obj.workspace_addon_path

    test_modules = _test_modules_special_cases(test_modules, workspace_addon_path)

    skip_test_modules = [m for m in skip_test_modules if m in test_modules]  # Filter out skip mods that arent requested
    if skip_test_modules:
        LOGGER.info("Skipping Tests for Modules:\n%s", ", ".join(["\t" + m for m in skip_test_modules]))
        test_modules = [m for m in test_modules if m not in skip_test_modules]

    if not test_modules:
        LOGGER.info("Nothing to Test. Skipping.")
        return

    test_module_list = ",".join(["/" + m for m in test_modules])
    module_list = ",".join(test_modules)

    LOGGER.info("Testing Odoo Modules:\n%s", "\n".join(sorted(["\t" + m for m in test_modules])))

    bootstrap_args = [
        "--test-enable",
        f"--init {module_list}",
    ]
    if re.search("(sale|account)", test_module_list, re.IGNORECASE):
        bootstrap_args[-1] += ",l10n_generic_coa"

    bootstrap_args.append(f"--test-tags {test_module_list}")
    bootstrap_args.append("--load-language en_US")
    launch_args = [
        f"-u {module_list}",
        "--test-enable",
        f"--log-level {odoo_log_level}",
        "--stop-after-init",
        "--no-http",
        f"--test-tags {test_module_list}",
    ]

    ret = launch_odoo(
        ctx=ctx,
        no_install_workspace_addons=True,
        extra_args=launch_args,
        extra_bootstrap_args=bootstrap_args,
        no_launch=True,
        multithread_worker_count=0,
        odoo_demo=True,
        no_update_source=True,
    )
    return typer_retuner(ret)
