"""Tests for the official Odoo upgrade command wrapper."""

from __future__ import annotations

import importlib
import os
import signal
import ssl
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

import pytest
from click import unstyle
from typer.testing import CliRunner

from godoo_cli.commands.root import main_cli

upgrade_command = importlib.import_module("godoo_cli.commands.upgrade")


def test_upgrade_forwards_arguments_and_inherits_process_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client receives exact arguments and runs from the caller environment."""
    monkeypatch.delenv("GODOO_UPGRADE_SSL", raising=False)
    monkeypatch.setenv("SSL_VERIFICATION", "0")
    monkeypatch.setenv("PRESERVED_FOR_OFFICIAL_CLIENT", "value")
    opened = Mock(return_value=BytesIO(b"official client"))
    child = Mock(return_value=Mock(wait=lambda: 0, pid=42))
    monkeypatch.setattr(upgrade_command, "urlopen", opened)
    monkeypatch.setattr(
        upgrade_command.subprocess,
        "Popen",
        child,
    )
    arguments = ["production", "--dbname", "database", "--target", "19.0", "--token", "secret", "-x"]

    assert upgrade_command._run_upgrade_client(arguments) == 0
    assert os.environ["SSL_VERIFICATION"] == "0"

    opened.assert_called_once_with(upgrade_command.UPGRADE_CLIENT_URL, timeout=60, context=None)
    command = child.call_args.args[0]
    assert command[0] == sys.executable
    assert command[2:] == arguments
    assert child.call_args.kwargs["start_new_session"] is True
    child_env = child.call_args.kwargs["env"]
    assert child_env["SSL_VERIFICATION"] == "1"
    assert child_env["PRESERVED_FOR_OFFICIAL_CLIENT"] == "value"
    assert not Path(command[1]).exists()


def test_upgrade_disables_verification_only_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compatibility switch applies to the downloader and official client."""
    opened = Mock(return_value=BytesIO(b"official client"))
    child = Mock(return_value=Mock(wait=lambda: 0, pid=42))
    monkeypatch.setenv("GODOO_UPGRADE_SSL", "0")
    monkeypatch.setenv("SSL_VERIFICATION", "1")
    monkeypatch.setattr(upgrade_command, "urlopen", opened)
    monkeypatch.setattr(upgrade_command.subprocess, "Popen", child)

    assert upgrade_command._run_upgrade_client(["status", "token"]) == 0

    context = opened.call_args.kwargs["context"]
    assert opened.call_args.args == (upgrade_command.UPGRADE_CLIENT_URL,)
    assert opened.call_args.kwargs["timeout"] == 60
    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE
    child_env = child.call_args.kwargs["env"]
    assert child_env["SSL_VERIFICATION"] == "0"
    assert child_env["GODOO_UPGRADE_SSL"] == "0"


def test_upgrade_preserves_client_exit_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wrapper returns the official client's exit status unchanged."""
    monkeypatch.setattr(upgrade_command, "urlopen", lambda *_args, **_kwargs: BytesIO(b""))
    monkeypatch.setattr(
        upgrade_command.subprocess,
        "Popen",
        lambda *_args, **_kwargs: Mock(wait=lambda: 23),
    )

    assert upgrade_command._run_upgrade_client(["status", "token"]) == 23


def test_upgrade_re_raises_child_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A signal-terminated client sends the same signal to the wrapper."""
    monkeypatch.setattr(upgrade_command, "urlopen", lambda *_args, **_kwargs: BytesIO(b""))
    monkeypatch.setattr(
        upgrade_command.subprocess,
        "Popen",
        lambda *_args, **_kwargs: Mock(wait=lambda: -signal.SIGTERM, pid=42),
    )
    send_signal = Mock()
    monkeypatch.setattr(upgrade_command.os, "kill", send_signal)
    monkeypatch.setattr(upgrade_command.signal, "signal", lambda *_args: None)

    assert upgrade_command._run_upgrade_client(["status", "token"]) == 128 + signal.SIGTERM
    send_signal.assert_called_once_with(os.getpid(), signal.SIGTERM)


def test_upgrade_forwards_parent_signals_and_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parent stop signals reach the client process group before it exits."""
    child = Mock(pid=42)
    installed_handlers = {}
    previous_handler = Mock()
    installed_handler_calls = []

    def install(signum: int, handler: object) -> object:
        installed_handler_calls.append((signum, handler))
        installed_handlers[signum] = handler
        return previous_handler

    def wait():
        installed_handlers[signal.SIGTERM](signal.SIGTERM, None)
        return -signal.SIGTERM

    child.wait.side_effect = wait
    monkeypatch.setattr(upgrade_command.subprocess, "Popen", lambda *_args, **_kwargs: child)
    monkeypatch.setattr(upgrade_command.signal, "signal", install)
    kill_group = Mock()
    monkeypatch.setattr(upgrade_command.os, "killpg", kill_group)
    monkeypatch.setattr(upgrade_command.os, "kill", Mock())

    assert upgrade_command._run_process(["python", "upgrade.py"]) == 128 + signal.SIGTERM

    kill_group.assert_called_once_with(42, signal.SIGTERM)
    child.wait.assert_called_once_with()
    term_handlers = [handler for signum, handler in installed_handler_calls if signum == signal.SIGTERM]
    assert len(term_handlers) == 2
    assert term_handlers[0] is not previous_handler
    assert term_handlers[1] is previous_handler


def test_upgrade_reports_download_error_without_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Download failures are reported without printing forwarded credentials."""
    monkeypatch.setattr(
        upgrade_command,
        "urlopen",
        Mock(side_effect=URLError("connection failed")),
    )
    runner = CliRunner()
    result = runner.invoke(main_cli(), ["upgrade", "status", "sensitive-token"])

    assert result.exit_code == 1
    assert "Could not download the official Odoo upgrade client." in result.output
    assert "sensitive-token" not in result.output


def test_upgrade_command_passthrough_and_help() -> None:
    """The root registers upgrade with passthrough options and focused help."""
    runner = CliRunner()
    root_result = runner.invoke(main_cli(), ["--help"], terminal_width=220)
    help_result = runner.invoke(main_cli(), ["upgrade", "--help"], terminal_width=220)

    assert root_result.exit_code == 0
    assert "upgrade" in unstyle(root_result.output)
    assert help_result.exit_code == 0
    assert "official odoo database upgrade client" in unstyle(help_result.output).lower()


@pytest.mark.parametrize(
    "arguments",
    [
        ["test", "--dbname", "database", "--target", "19.0", "--future-official-option", "value", "-x"],
        ["test", "--db", "database", "--tar", "19.0"],
        ["test", "-d", "database", "-xt19.0"],
        ["test", "--dump", "source dump/odoo.dump", "--target", "19.0", "--contract", "contract-value"],
    ],
)
def test_upgrade_cli_forwards_official_options(monkeypatch: pytest.MonkeyPatch, arguments: list[str]) -> None:
    """Unknown Odoo options pass through the gOdoo command parser unchanged."""
    monkeypatch.setattr(upgrade_command, "urlopen", lambda *_args, **_kwargs: BytesIO(b""))
    child = Mock(return_value=Mock(wait=lambda: 0, pid=42))
    monkeypatch.setattr(
        upgrade_command.subprocess,
        "Popen",
        child,
    )
    result = CliRunner().invoke(main_cli(), ["upgrade", *arguments])

    assert result.exit_code == 0, result.output
    assert child.call_args.args[0][2:] == arguments


def test_upgrade_cli_preserves_nonzero_client_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """The public command exits with the official client's status."""
    monkeypatch.setattr(upgrade_command, "urlopen", lambda *_args, **_kwargs: BytesIO(b""))
    child = Mock(return_value=Mock(wait=lambda: 23, pid=42))
    monkeypatch.setattr(upgrade_command.subprocess, "Popen", child)

    result = CliRunner().invoke(main_cli(), ["upgrade", "status", "token"])

    assert result.exit_code == 23, result.output
