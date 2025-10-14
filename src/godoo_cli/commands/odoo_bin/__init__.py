"""CLI command to Bootstrap or launch Odoo using odoo-bin."""

from .bootstrap import bootstrap_odoo
from .godoo_test import *
from .launch import launch_import, launch_odoo
from .shell import odoo_shell, odoo_shell_run_script, odoo_shell_uninstall_modules
