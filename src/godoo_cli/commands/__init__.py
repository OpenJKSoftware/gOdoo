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

from .config import set_odoo_config
from .db import db_cli_app, dump_database, duplicate_cow, load_database, reset_database_from_template, reset_odoo_state
from .lifecycle import dev_odoo, ensure_odoo_runtime
from .odoo_bin import (
    bootstrap_odoo,
    launch_import,
    launch_odoo,
    odoo_load_test_data,
    odoo_run_tests,
    odoo_shell,
    odoo_shell_run_script,
    odoo_shell_uninstall_modules,
    prepare_odoo,
    test_cli_app,
)
from .rpc import rpc_cli_app
from .source_get import source_cli_app
