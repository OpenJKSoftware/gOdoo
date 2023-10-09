from .backup import backup_cli_app
from .bootstrap import bootstrap_odoo
from .config import set_odoo_config
from .db import db_cli_app
from .launch import launch_import, launch_odoo
from .rpc import rpc_cli_app
from .shell import odoo_shell, uninstall_modules
from .source_get import source_cli_app
from .test import test_cli_app
from .test.test_run import odoo_run_tests
