"""Expose commands that operate through Odoo RPC."""

import typer

from .cli import rpc_cli_app
from .importer import import_to_odoo as import_to_odoo
