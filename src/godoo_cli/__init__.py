"""gOdoo CLI package for managing Odoo instances.

This package provides a comprehensive command-line interface for managing Odoo instances,
including functionality for:
- Launching and bootstrapping Odoo instances
- Managing databases and configurations
- Handling RPC operations
- Managing source code and addons
- Running tests and shell commands
"""

from .__about__ import __version__
from .cli import launch_cli
