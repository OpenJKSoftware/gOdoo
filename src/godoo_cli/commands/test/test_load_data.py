import logging
from pathlib import Path
from typing import Annotated, Optional

import typer

from ...cli_common import CommonCLI
from ...helpers.modules import GodooModules, get_addon_paths
from ...helpers.system import run_cmd
from ..db.connection import DBConnection
from ..launch import bootstrap_and_prep_launch_cmd
from ..shell.shell import odoo_shell

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)


def odoo_load_test_data(
    test_modules: Annotated[
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
    thirdparty_addon_path: Annotated[Path, CLI.odoo_paths.thirdparty_addon_path],
    odoo_conf_path: Annotated[Path, CLI.odoo_paths.conf_path],
    db_filter: Annotated[str, CLI.database.db_filter],
    db_name: Annotated[str, CLI.database.db_name],
    db_user: Annotated[str, CLI.database.db_user],
    db_host: Annotated[str, CLI.database.db_host] = "",
    db_port: Annotated[int, CLI.database.db_port] = 0,
    db_password: Annotated[str, CLI.database.db_password] = "",
    extra_launch_args: Annotated[Optional[str], CLI.odoo_launch.extra_cmd_args] = None,
    extra_bootstrap_args: Annotated[Optional[str], CLI.odoo_launch.extra_cmd_args_bootstrap] = None,
    odoo_log_level: Annotated[str, typer.Option(help="Log level")] = "test",
    multithread_worker_count: Annotated[int, CLI.odoo_launch.multithread_worker_count] = 2,
):
    """Loads test data from test/data.py of given modules into Odoo DB.

    Makes sure Odoo is bootstrapped with the given modules and then
    calls `tests.data.generate_test_data(env)` for each module.
    """
    addon_paths = get_addon_paths(
        odoo_main_repo=odoo_main_path,
        workspace_addon_path=workspace_addon_path,
        thirdparty_addon_path=thirdparty_addon_path,
    )
    godoo_test_modules = list(GodooModules(addon_paths).get_modules(test_modules))
    test_module_names = [m.name for m in godoo_test_modules]
    module_list_csv = ",".join(test_module_names)
    LOGGER.info("Installing Test data for Odoo Modules:\n%s", sorted(test_module_names))

    missing = False
    for module in godoo_test_modules:
        data_file = module.path / "tests" / "data.py"
        if not data_file.exists():
            missing = True
            LOGGER.error("Test Data.py file not found for module: %s", module)
    if missing:
        return CLI.returner(1)

    bootstrap_args = [
        f"--init {module_list_csv}",
    ]

    launch_args = [
        f"-u {module_list_csv}",
        f"--log-level {odoo_log_level}",
        "--stop-after-init",
        "--no-http",
    ]

    if extra_launch_args:
        launch_args = extra_launch_args + launch_args
    if extra_bootstrap_args:
        bootstrap_args = extra_bootstrap_args + bootstrap_args

    db_connection = DBConnection(
        hostname=db_host,
        port=db_port,
        username=db_user,
        password=db_password,
        db_name=db_name,
    )

    launch_cmd = bootstrap_and_prep_launch_cmd(
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
        multithread_worker_count=multithread_worker_count,
        odoo_demo=False,
        launch_or_bootstrap=True,
    )
    if isinstance(launch_cmd, str):
        LOGGER.info("Ensuring modules are intalled")
        launch_cmd = run_cmd(launch_cmd, shell=True).returncode

    if launch_cmd != 0:
        LOGGER.error("Failed to Launch or Bootstrap Odoo")
        LOGGER.debug("Launch Return: %s", launch_cmd)
        return CLI.returner(launch_cmd)

    for module in godoo_test_modules:
        load_cmd = f"from odoo.addons.{module.name}.tests.data import generate_test_data; generate_test_data(env);env.cr.commit()"
        LOGGER.info("Calling Test Data Generator for Module: %s", module)
        ret = odoo_shell(pipe_in_command=load_cmd, odoo_main_path=odoo_main_path, odoo_conf_path=odoo_conf_path)
        if ret != 0:
            LOGGER.error("Failed to generate test data for module: %s", module)
            return CLI.returner(ret)
