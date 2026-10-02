"""Expose gOdoo CLI command groups."""

from .db import db_cli_app
from .rpc import rpc_cli_app
from .run import run_odoo
from .runtime import runtime_cli_app
from .test import test_cli_app
from .upgrade import upgrade
from .workspace import workspace_cli_app
