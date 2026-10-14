"""Exercise scripts with the environment supplied by Odoo shell."""

import runpy
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from godoo_cli.commands.runtime.shell import SHELL_SCRIPTS_PATH


@pytest.mark.parametrize(
    ("banner_text", "expected"), [(None, "Development"), ("", "Development"), ("QA instance", "QA instance")]
)
def test_banner_script_uses_development_default(
    monkeypatch: pytest.MonkeyPatch, banner_text: str | None, expected: str
):
    """Unset and empty overrides render Development; explicit text is preserved."""
    if banner_text is None:
        monkeypatch.delenv("ODOO_BANNER_TEXT", raising=False)
    else:
        monkeypatch.setenv("ODOO_BANNER_TEXT", banner_text)
    monkeypatch.setitem(sys.modules, "odoo", SimpleNamespace(api=SimpleNamespace(Environment=object)))
    environment = MagicMock()
    view_model = MagicMock()
    module_model = MagicMock()
    environment.__getitem__.side_effect = {"ir.ui.view": view_model, "ir.module.module": module_model}.__getitem__
    environment.ref.return_value = SimpleNamespace(id=1, active=False)
    view_model.search.return_value = False
    module_model.search.return_value = False

    runpy.run_path(str(SHELL_SCRIPTS_PATH / "odoo_banner.py"), init_globals={"env": environment})

    view_model.create.assert_called_once()
    view = view_model.create.call_args.args[0]
    assert expected in view["arch_db"]
    assert "None" not in view["arch_db"]
    environment.cr.commit.assert_called_once()
