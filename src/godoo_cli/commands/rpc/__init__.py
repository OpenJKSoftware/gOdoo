"""
Things that interact with Odoo RPC.
Must Provide username and Password
"""
import typer

from .cli import rpc_cli_app
from .importer import import_to_odoo as import_to_odoo
