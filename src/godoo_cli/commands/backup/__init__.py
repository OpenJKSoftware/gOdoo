"""Backup and restore functionality for Odoo instances.

This package provides commands for managing Odoo database backups, including:
- Creating database dumps
- Loading database backups
- Pulling backups from remote sources
- Managing backup files and configurations
"""

from .cli import backup_cli_app
