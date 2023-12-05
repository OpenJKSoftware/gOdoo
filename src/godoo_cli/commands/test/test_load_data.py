import logging
from typing import List

import typer

from ...cli_common import CommonCLI
from ...helpers.system import run_cmd
from ..db.connection import DBConnection
from ..launch import pre_launch
from ..shell import odoo_shell

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)


@CLI.arg_annotator
def odoo_load_test_data(
    test_modules: List[str] = typer.Argument(
        ...,
        help="Modules to install and test (Use 'all' for all Workspace modules), ('changes:<branch> to compare git changes)",
    ),
    odoo_main_path=CLI.odoo_paths.bin_path,
    workspace_addon_path=CLI.odoo_paths.workspace_addon_path,
    thirdparty_addon_path=CLI.odoo_paths.thirdparty_addon_path,
    odoo_conf_path=CLI.odoo_paths.conf_path,
    extra_launch_args=CLI.odoo_launch.extra_cmd_args,
    db_filter=CLI.database.db_filter,
    db_host=CLI.database.db_host,
    db_port=CLI.database.db_port,
    db_name=CLI.database.db_name,
    db_user=CLI.database.db_user,
    db_password=CLI.database.db_password,
    odoo_log_level: str = typer.Option("test", help="Log level"),
    multithread_worker_count=CLI.odoo_launch.multithread_worker_count,
):
    """Run tests.data.generate_test_data fnction in Odoo shell for given modules. Ensures Modules are installed and odoo is bootstrapped"""

    if not test_modules:
        LOGGER.info("Nothing to Test. Skipping.")
        return

    module_list_csv = ",".join(test_modules)

    LOGGER.info("Installing Test data for Odoo Modules:\n%s", sorted(test_modules))

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

    for module in test_modules:
        load_cmd = (
            f"from odoo.addons.{module}.tests.data import generate_test_data; generate_test_data(env);env.cr.commit()"
        )
        LOGGER.info("Calling Test Data Generator for Module: %s", module)
        ret = odoo_shell(pipe_in_command=load_cmd, odoo_main_path=odoo_main_path, odoo_conf_path=odoo_conf_path)
        if ret != 0:
            LOGGER.error("Failed to generate test data for module: %s", module)
            return CLI.returner(ret)
        LOGGER.info("Test Data Generated for Module: %s", module)
