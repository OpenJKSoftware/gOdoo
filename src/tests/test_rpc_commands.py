"""Regression tests for RPC command boundaries."""

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import typer
from godoo_rpc import OdooApiWrapper

from godoo_cli.commands.rpc import config_parameters, importer, modules, translations
from godoo_cli.helpers.cli import check_dangerous_command


def test_import_reports_missing_path_as_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Missing Path values can be presented in an error message."""
    missing_path = tmp_path / "missing data"
    monkeypatch.setattr(importer, "wait_for_odoo", lambda **_kwargs: object())

    with pytest.raises(ValueError, match="missing data"):
        importer.import_to_odoo([missing_path], "host", "database", "user", "password")


def test_import_disables_image_search_with_empty_regex(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Single-file imports must not search their non-existent img directory."""
    data_file = tmp_path / "module.csv"
    data_file.touch()
    calls = []
    monkeypatch.setattr(importer, "wait_for_odoo", lambda **_kwargs: object())
    monkeypatch.setattr(importer, "import_data", lambda **kwargs: calls.append(kwargs))

    importer.import_to_odoo([data_file], "host", "database", "user", "password")

    assert calls[0]["product_image_regex"] == ""


def test_config_parameter_rejects_unauthenticated_session(monkeypatch: pytest.MonkeyPatch):
    """RPC commands fail explicitly when the library returns no environment."""
    odoo_api = SimpleNamespace(session=SimpleNamespace(env=None))
    monkeypatch.setattr(config_parameters, "wait_for_odoo", lambda **_kwargs: odoo_api)

    with pytest.raises(RuntimeError, match="not authenticated"):
        config_parameters.set_config_parameter("key", "value", "host", "database", "user", "password")


def test_module_lookup_rejects_unauthenticated_session():
    """Module lookup has the same explicit authentication boundary."""
    odoo_api = cast(OdooApiWrapper, SimpleNamespace(session=SimpleNamespace(env=None)))

    with pytest.raises(RuntimeError, match="not authenticated"):
        modules.rpc_get_modules(odoo_api, "sale")


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
