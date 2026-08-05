"""Database management functionality for Odoo instances.

This package provides commands for managing Odoo databases, including:
- Database connection handling
- Password management
- Database queries and operations
- Database configuration
"""

from .archive import dump_database, load_database
from .cli import db_cli_app
from .cow import duplicate_cow
from .reset import reset_database_from_template, reset_odoo_state
