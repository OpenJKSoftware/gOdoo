"""Check logging for terminals and captured output."""

import io
import logging
from unittest.mock import Mock

import pytest
from rich.logging import RichHandler

from godoo_cli.helpers import system


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("verbose", [False, True])
def test_logging_adapts_to_stderr(terminal: bool, verbose: bool, monkeypatch: pytest.MonkeyPatch):
    stream = io.StringIO()
    install_traceback = Mock()
    monkeypatch.setattr(stream, "isatty", lambda: terminal)
    monkeypatch.setattr(system.sys, "stderr", stream)
    monkeypatch.setattr(system, "install_rich_traceback", install_traceback)
    monkeypatch.setattr(logging.root, "handlers", [])
    monkeypatch.setattr(logging.root, "level", logging.root.level)
    monkeypatch.setenv("COLUMNS", "30")
    monkeypatch.setenv("FORCE_COLOR", "1")

    system.set_logging(verbose=verbose)

    handler = logging.root.handlers[0]
    assert isinstance(handler, RichHandler) is terminal
    assert logging.root.level == (logging.DEBUG if verbose else logging.INFO)
    assert install_traceback.called is (terminal and verbose)

    message = "Preparing runtime database and checking installed modules before startup."
    record = logging.LogRecord("godoo.test", logging.INFO, __file__, 1, message, (), None)
    handler.handle(record)
    output = stream.getvalue()
    if terminal:
        assert isinstance(handler, RichHandler)
        assert handler.rich_tracebacks is verbose
        assert handler.markup is verbose
    else:
        assert type(handler) is logging.StreamHandler
        assert len(output.splitlines()) == 1
        assert message in output
        assert "INFO" in output
        assert ("godoo.test:" in output) is verbose
        assert "\x1b" not in output
        assert "[italic" not in output
