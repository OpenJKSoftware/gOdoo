"""Safe execution helpers for Odoo command lines."""

import logging
import shlex
import subprocess
from collections.abc import Sequence
from typing import Any, Union

LOGGER = logging.getLogger(__name__)

OdooCommand = Union[str, Sequence[str]]


def odoo_command_argv(command: OdooCommand) -> list[str]:
    """Normalize a legacy command string or argument sequence."""
    return shlex.split(command) if isinstance(command, str) else list(command)


def run_odoo_command(command: OdooCommand, **kwargs: Any) -> subprocess.CompletedProcess:
    """Run Odoo without a shell and return its process result.

    Keyword arguments are forwarded to :func:`subprocess.run`, which allows
    callers to provide stdin or script content without a shell pipeline.
    """
    if "shell" in kwargs:
        message = "Odoo commands must not be run through a shell"
        raise ValueError(message)
    argv = odoo_command_argv(command)
    LOGGER.debug("Running Odoo command: %s", argv)
    process = subprocess.run(argv, check=False, **kwargs)
    LOGGER.debug("Odoo command returned %s", process.returncode)
    return process
