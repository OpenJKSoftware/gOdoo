"""Command modules for the gOdoo CLI.

This package contains all the command modules that provide the core functionality
of the gOdoo CLI, including:
- Backup and restore operations
- Database management
- RPC operations
- Shell commands
- Source code management
- Test execution
"""

from .backup import backup_cli_app
from .bootstrap import bootstrap_odoo
from .config import set_odoo_config
from .db import db_cli_app
from .launch import launch_import, launch_odoo
from .rpc import rpc_cli_app
from .shell.shell import odoo_shell, odoo_shell_run_script, uninstall_modules
from .source_get import source_cli_app
from .test import test_cli_app
from .test.test_run import odoo_run_tests
