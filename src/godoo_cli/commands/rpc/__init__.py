"""RPC command module for Odoo interaction.

This module provides RPC (Remote Procedure Call) functionality for interacting with Odoo instances.
It includes commands for data import, module management, and other remote operations.
All commands require proper authentication with username and password.

Typical usage example:
    ```python
    from godoo_cli.commands.rpc import import_to_odoo

    # Import data into Odoo
    import_to_odoo(read_paths=['data.json'], rpc_host='localhost', ...)
    ```
"""

import typer

from .cli import rpc_cli_app
from .importer import import_to_odoo as import_to_odoo
