"""Regression tests for process command execution."""

from types import SimpleNamespace
from unittest.mock import patch

from godoo_cli.helpers.system import run_cmd


def test_run_cmd_uses_no_shell_for_argument_vectors():
    """A path with spaces remains a single argument when passed as an argv vector."""
    command = ["pip", "install", "-r", "/tmp/odoo source/requirements.txt"]
    with patch("godoo_cli.helpers.system.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
        run_cmd(command)

    run.assert_called_once_with(command, shell=False)
