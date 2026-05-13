"""CLI command to Bootstrap or launch Odoo using odoo-bin."""

from .bootstrap import bootstrap_odoo
from .godoo_test import odoo_get_changed_modules, odoo_load_test_data, odoo_run_tests, test_cli_app
from .launch import launch_import, launch_odoo
from .shell import odoo_shell, odoo_shell_run_script, odoo_shell_uninstall_modules
