"""CLI command to Bootstrap or launch Odoo using odoo-bin."""

from .bootstrap import bootstrap_odoo
from .launch import launch_import, launch_odoo
from .shell import odoo_shell, odoo_shell_run_script, odoo_shell_uninstall_modules
from .test.cli import test_cli_app
from .test.test_load_data import odoo_load_test_data
from .test.test_run import odoo_get_changed_modules, odoo_pregenerate_assets, odoo_run_tests
