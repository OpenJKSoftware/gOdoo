"""Regression tests for RPC command boundaries."""

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

from godoo_cli.commands.configuration import check_dangerous_command
from godoo_cli.commands.rpc import importer, modules, translations


def test_import_reports_missing_path_as_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Missing Path values can be presented in an error message."""
    missing_path = tmp_path / "missing data"
    monkeypatch.setattr(importer, "wait_for_odoo", lambda **_kwargs: object())

    with pytest.raises(ValueError, match="missing data"):
        importer.import_to_odoo([missing_path], "host", "database", "user", "password")


def test_uninstall_returns_success_after_uninstall(monkeypatch: pytest.MonkeyPatch):
    """A successful uninstall must not fall through to the failure return path."""
    record = SimpleNamespace(id=1, name="sale", state="installed")
    uninstalled = []

    class ModuleRecordset:
        def __iter__(self) -> Iterator[SimpleNamespace]:
            return iter([record])

        def browse(self, ids: list[int]) -> "ModuleRecordset":
            assert ids == [1]
            return self

        def button_immediate_uninstall(self) -> None:
            uninstalled.append(True)

    monkeypatch.setattr(modules, "wait_for_odoo", lambda **_kwargs: object())
    monkeypatch.setattr(modules, "rpc_get_modules", lambda *_args: ModuleRecordset())
    monkeypatch.setattr(modules.CLI, "returner", lambda _code: pytest.fail("unexpected failure return"))

    assert modules.uninstall_modules("sale", "host", "database", "user", "password") is None
    assert uninstalled == [True]


def test_translation_lookup_uses_comma_separated_module_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The RPC query API receives its documented string query format."""
    godoo_module = SimpleNamespace(name="sale", path=tmp_path)
    calls = []
    monkeypatch.setattr(
        translations,
        "GodooModules",
        lambda _path: SimpleNamespace(get_modules=lambda _names: [godoo_module]),
    )
    monkeypatch.setattr(translations, "wait_for_odoo", lambda **_kwargs: object())
    monkeypatch.setattr(
        translations,
        "rpc_get_modules",
        lambda _odoo_api, query, valid_names: calls.append((query, valid_names)) or [object()],
    )
    monkeypatch.setattr(translations, "_dump_translations", lambda **_kwargs: None)

    translations.dump_translations(["sale"], tmp_path, "host", "database", "user", "password")

    assert calls == [("sale", ["sale"])]


def test_dangerous_command_exits_with_integer_status(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    """The development safety guard emits its message and an integer status."""
    monkeypatch.delenv("WORKSPACE_IS_DEV", raising=False)

    with pytest.raises(typer.Exit) as error:
        check_dangerous_command()

    assert error.value.exit_code == 1
    assert "Only allowed in Dev Mode" in capsys.readouterr().err
