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
from .config import set_odoo_config
from .db import db_cli_app
from .odoo_bin import (
    bootstrap_odoo,
    launch_import,
    launch_odoo,
    odoo_load_test_data,
    odoo_run_tests,
    odoo_shell,
    odoo_shell_run_script,
    odoo_shell_uninstall_modules,
    test_cli_app,
)
from .rpc import rpc_cli_app
from .source_get import source_cli_app
