"""CLI helper functions module.

This module provides utility functions for command-line interface operations,
including user interaction and command validation. It helps ensure safe
execution of potentially dangerous operations.
"""

import logging
import os

import typer

LOGGER = logging.getLogger(__name__)


def check_dangerous_command():
    """Check if a potentially dangerous command should be executed.

    This function checks if the environment is marked as a development
    environment before allowing potentially dangerous operations. It helps
    prevent accidental execution of risky commands in production.

    Raises:
        typer.Exit: If the environment is not marked as development.
    """
    isdev = str(os.getenv("WORKSPACE_IS_DEV"))
    if isdev.lower() != "true":
        msg = "This is a dangerous command. Only allowed in Dev Mode."
        LOGGER.error(msg)
        raise typer.Exit(msg)
