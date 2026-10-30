"""Focused helpers used by runtime initialization."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from godoo_cli.database.state import DbBootstrapStatus
from godoo_cli.models import GodooConfig
from godoo_cli.runtime import lifecycle


@pytest.fixture(autouse=True)
def supported_database_major(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a supported runtime for tests with an isolated fake configuration."""
    monkeypatch.setattr(lifecycle, "require_runtime_database_major", lambda _config: 19)


def _config(tmp_path: Path) -> GodooConfig:
    return GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        odoo_conf_path=tmp_path / "odoo.conf",
        db_name="runtime",
        db_user="odoo",
        data_dir=tmp_path / "data",
    )


def test_native_paths_are_dormant_without_native_selections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lifecycle, "run_odoo_command", lambda *_args, **_kwargs: pytest.fail("must not start Odoo"))
    assert (
        lifecycle.reconcile_modules(
            _config(tmp_path), [], [], upgrade_path=[tmp_path], pre_upgrade_scripts=[tmp_path / "pre.py"]
        )
        == 0
    )


def test_reconcile_modules_passes_selected_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[str] = []

    def build_command(_config: GodooConfig, arguments: list[str], **kwargs: object) -> list[str]:
        assert kwargs["upgrade_workspace_modules"] is False
        return arguments

    monkeypatch.setattr(lifecycle, "build_launch_command", build_command)
    monkeypatch.setattr(
        lifecycle, "run_odoo_command", lambda command: observed.extend(command) or SimpleNamespace(returncode=0)
    )
    lifecycle.reconcile_modules(_config(tmp_path), ["sale,stock"], ["web"])
    assert observed[observed.index("--update") + 1] == "sale,stock"
    assert observed[observed.index("--init") + 1] == "web"


def test_ensure_runtime_prepares_and_passes_bootstrap_policy(tmp_path: Path) -> None:
    calls: list[str] = []
    observed: dict[str, object] = {}

    def bootstrap(_config: GodooConfig, **kwargs: object) -> int:
        calls.append("bootstrap")
        observed.update(kwargs)
        return 0

    assert lifecycle.ensure_runtime(
        _config(tmp_path),
        preparer=lambda _config: calls.append("prepare"),
        status_getter=lambda _connection: DbBootstrapStatus.NO_DB,
        bootstrapper=bootstrap,
    )
    assert calls == ["prepare", "bootstrap"]
    assert observed["extra_cmd_args"] == ["--without-demo"]


def test_ensure_runtime_reports_native_bootstrap_failure(tmp_path: Path) -> None:
    with pytest.raises(lifecycle.LifecycleBootstrapError) as error:
        lifecycle.ensure_runtime(
            _config(tmp_path),
            preparer=lambda _config: None,
            status_getter=lambda _connection: DbBootstrapStatus.EMPTY_DB,
            bootstrapper=lambda *_args, **_kwargs: 17,
        )
    assert error.value.return_code == 17


def test_reconcile_preflight_includes_selected_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[str] = []
    monkeypatch.setattr(lifecycle, "preflight_for_config", lambda _config, args, **_kwargs: observed.extend(args))
    lifecycle.preflight_reconcile_dependencies(_config(tmp_path), ["sale,stock"], ["web"])
    assert observed == ["--update", "sale,stock", "--init", "web"]


def test_reconcile_without_selection_does_not_run_odoo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Initialization without module selections must not trigger implicit upgrades."""
    monkeypatch.setattr(
        lifecycle, "build_launch_command", lambda *_args, **_kwargs: pytest.fail("unrequested module reconciliation")
    )
    assert lifecycle.reconcile_modules(_config(tmp_path), None, None) == 0
