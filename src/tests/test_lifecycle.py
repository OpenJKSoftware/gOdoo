from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.commands import lifecycle as lifecycle_commands
from godoo_cli.commands.db.query import DbBootstrapStatus
from godoo_cli.lifecycle import LifecycleBootstrapError, ensure_runtime
from godoo_cli.models import GodooConfig


def _config(tmp_path: Path) -> GodooConfig:
    return GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        db_name="runtime",
    )


def test_ensure_runtime_prepares_and_skips_existing_database(tmp_path: Path):
    calls: list[str] = []

    created = ensure_runtime(
        _config(tmp_path),
        preparer=lambda _config: calls.append("prepare"),
        status_getter=lambda _connection: DbBootstrapStatus.BOOTSTRAPPED,
        bootstrapper=lambda *_args, **_kwargs: calls.append("bootstrap") or 0,
    )

    assert created is False
    assert calls == ["prepare"]


def test_ensure_runtime_syncs_and_bootstraps_missing_database(tmp_path: Path):
    calls: list[str] = []

    created = ensure_runtime(
        _config(tmp_path),
        source_synchronizer=lambda: calls.append("sync"),
        preparer=lambda _config: calls.append("prepare"),
        status_getter=lambda _connection: DbBootstrapStatus.NO_DB,
        bootstrapper=lambda *_args, **_kwargs: calls.append("bootstrap") or 0,
    )

    assert created is True
    assert calls == ["sync", "prepare", "bootstrap"]


def test_ensure_runtime_reports_native_bootstrap_failure(tmp_path: Path):
    with pytest.raises(LifecycleBootstrapError) as error:
        ensure_runtime(
            _config(tmp_path),
            preparer=lambda _config: None,
            status_getter=lambda _connection: DbBootstrapStatus.EMPTY_DB,
            bootstrapper=lambda *_args, **_kwargs: 17,
        )

    assert error.value.return_code == 17


@pytest.mark.parametrize(
    ("stage_value", "password_value", "expected"),
    [
        ("false", "false", (False, False)),
        ("0", "0", (False, False)),
        ("true", "1", (True, True)),
    ],
)
def test_dev_parses_post_bootstrap_environment_flags(
    monkeypatch: pytest.MonkeyPatch,
    stage_value: str,
    password_value: str,
    expected: tuple[bool, bool],
):
    observed: dict[str, bool] = {}
    app = typer.Typer()
    app.command()(lifecycle_commands.dev_odoo)
    monkeypatch.setattr(lifecycle_commands, "_ensure_config", lambda *_args, **_kwargs: True)

    def run_hooks(
        _config: GodooConfig,
        *,
        staging: bool,
        set_dev_password: bool,
        migrations_dir: Path,
    ) -> int:
        del migrations_dir
        observed["staging"] = staging
        observed["set_dev_password"] = set_dev_password
        return 0

    monkeypatch.setattr(lifecycle_commands, "_run_devcontainer_hooks", run_hooks)
    result = CliRunner().invoke(
        app,
        ["--no-launch"],
        env={
            "ODOO_MAIN_FOLDER": "/tmp/odoo",
            "ODOO_WORKSPACE_ADDON_LOCATION": "/tmp/addons",
            "ODOO_THIRDPARTY_LOCATION": "/tmp/thirdparty",
            "ODOO_CONF_PATH": "/tmp/odoo.conf",
            "ODOO_DB_FILTER": ".*",
            "ODOO_MAIN_DB": "runtime",
            "ODOO_DB_USER": "odoo",
            "GODOO_LAUNCH_STAGE": stage_value,
            "GODOO_DEV_SET_PW": password_value,
        },
    )

    assert result.exit_code == 0, result.output
    assert (observed["staging"], observed["set_dev_password"]) == expected
