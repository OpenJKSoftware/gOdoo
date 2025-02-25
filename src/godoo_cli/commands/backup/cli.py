"""CLI interface for backup and restore operations.

This module provides the command-line interface for managing Odoo database backups,
including commands for creating, loading, and managing backup files.
"""

import typer

from .dump import dump_instance
from .load import load_instance_data
from .pull import InstancePuller


def backup_cli_app():
    """Create and configure the backup CLI application.

    This function sets up the command-line interface for backup operations,
    including commands for:
    - Creating database dumps
    - Loading database backups
    - Pulling backups from remote sources

    Returns:
        typer.Typer: The configured CLI application instance.
    """
    app = typer.Typer(
        no_args_is_help=True,
        help="Functions around Backing up and Restoring Odoo",
    )
    puller = InstancePuller()

    app.command("pull")(puller.pull_instance_data)
    app.command("load")(load_instance_data)
    app.command("dump")(dump_instance)

    return app
