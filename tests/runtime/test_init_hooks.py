"""Focused CLI validation for lifecycle hooks and native upgrade inputs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from click import unstyle
from typer.testing import CliRunner

from godoo_cli.commands.runtime import init as runtime_init
from godoo_cli.models import GodooConfig
from godoo_cli.runtime.lifecycle import LifecycleOutcome
from godoo_cli.runtime.locks import runtime_readiness_marker, write_runtime_lifecycle


def _app() -> typer.Typer:
    app = typer.Typer()
    app.command()(runtime_init.deployment_init_odoo_runtime)
    return app


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "ODOO_MAIN_FOLDER": str(tmp_path / "odoo"),
        "ODOO_WORKSPACE_ADDON_LOCATION": str(tmp_path / "addons"),
        "ODOO_CONF_PATH": str(tmp_path / "odoo.conf"),
        "ODOO_DB_FILTER": ".*",
        "ODOO_MAIN_DB": "runtime",
        "ODOO_DB_USER": "odoo",
    }


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


def test_native_upgrade_inputs_are_validated_before_runtime_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime_init, "resolve_command_config", lambda **_kwargs: pytest.fail("config must not be read")
    )

    result = CliRunner().invoke(_app(), ["--upgrade-path", str(tmp_path / "missing")], env=_environment(tmp_path))

    assert result.exit_code == 2, result.output
    assert "Native upgrade directory does not exist" in result.output


def test_native_upgrade_path_requires_update_before_runtime_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upgrade_path = tmp_path / "upgrades"
    upgrade_path.mkdir()
    monkeypatch.setattr(
        runtime_init, "resolve_command_config", lambda **_kwargs: pytest.fail("config must not be read")
    )

    result = CliRunner().invoke(_app(), ["--upgrade-path", str(upgrade_path)], env=_environment(tmp_path))

    assert result.exit_code == 2, result.output
    assert "require at least one --update" in unstyle(result.output)


def test_after_restore_hooks_are_rejected_before_native_pre_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "pre.py"
    script.write_text("# native pre-upgrade\n", encoding="utf-8")
    hook_dir = tmp_path / "hooks"
    hook_dir.mkdir()
    (hook_dir / "restore.py").write_text(
        "from godoo_cli.runtime.hooks import Hook\n"
        "HOOK = Hook(id='restore', phase='after_restore', on=('restored',))\n"
        "def run(ctx): pass\n",
        encoding="utf-8",
    )
    config = _config(tmp_path)
    monkeypatch.setattr(runtime_init, "resolve_command_config", lambda **_kwargs: config)
    monkeypatch.setattr(runtime_init, "require_odoo_version", lambda *_args: SimpleNamespace(major=19, raw="19.0"))
    monkeypatch.setattr(
        runtime_init, "deployment_init", lambda *_args, **_kwargs: pytest.fail("must not mutate runtime")
    )

    result = CliRunner().invoke(
        _app(),
        [
            "--seed",
            str(tmp_path / "upgraded.zip"),
            "--update",
            "sale",
            "--pre-upgrade-script",
            str(script),
            "--hooks-dir",
            str(hook_dir),
        ],
        env=_environment(tmp_path),
    )

    assert result.exit_code == 2, result.output
    assert "after_restore hook" in result.output


def test_invalid_hook_declaration_fails_before_runtime_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook_dir = tmp_path / "hooks"
    hook_dir.mkdir()
    (hook_dir / "invalid.py").write_text("def run(ctx): pass\n", encoding="utf-8")
    monkeypatch.setattr(
        runtime_init, "resolve_command_config", lambda **_kwargs: pytest.fail("config must not be read")
    )

    result = CliRunner().invoke(_app(), ["--hooks-dir", str(hook_dir)], env=_environment(tmp_path))

    assert result.exit_code == 2, result.output
    assert "HOOK" in result.output


def test_prepare_hooks_merge_cli_and_environment_requests_on_every_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook_dir = tmp_path / "hooks"
    hook_dir.mkdir()
    (hook_dir / "10_first.py").write_text(
        "from godoo_cli.runtime.hooks import Hook\n"
        "HOOK = Hook(id='first', phase='prepare')\n"
        "def run(ctx): ctx.request_modules(update=['stock', 'sale'], install=['crm'])\n",
        encoding="utf-8",
    )
    (hook_dir / "20_second.py").write_text(
        "from godoo_cli.runtime.hooks import Hook\n"
        "HOOK = Hook(id='second', phase='prepare')\n"
        "def run(ctx): ctx.request_modules(update=['account'], install=['crm', 'helpdesk'])\n",
        encoding="utf-8",
    )
    config = _config(tmp_path)
    preflight: list[tuple[list[str] | None, list[str] | None]] = []
    reconciled: list[tuple[list[str] | None, list[str] | None]] = []
    monkeypatch.setattr(runtime_init, "resolve_command_config", lambda **_kwargs: config)
    monkeypatch.setattr(runtime_init, "require_odoo_version", lambda *_args: SimpleNamespace(major=19, raw="19.0"))
    monkeypatch.setattr(runtime_init, "x_sendfile_enabled", lambda *_args: False)
    monkeypatch.setattr(
        runtime_init,
        "preflight_reconcile_dependencies",
        lambda _config, update, install, **_kwargs: preflight.append((update, install)),
    )
    monkeypatch.setattr(
        runtime_init,
        "reconcile_runtime",
        lambda _config, *, reconciler, **_kwargs: reconciler(config),
    )
    monkeypatch.setattr(
        runtime_init,
        "reconcile_modules",
        lambda _config, update, install, **_kwargs: reconciled.append((update, install)) or 0,
    )

    def deployment(current: GodooConfig, **kwargs: object) -> tuple[LifecycleOutcome, int]:
        kwargs["preflight"](current)  # type: ignore[operator]
        kwargs["reconciler"](current)  # type: ignore[operator]
        return LifecycleOutcome.READY, 0

    monkeypatch.setattr(runtime_init, "deployment_init", deployment)
    environment = _environment(tmp_path) | {"GODOO_RECONCILE_UPDATE": "stock"}
    arguments = ["--update", "sale", "--install", "project", "--hooks-dir", str(hook_dir)]

    assert CliRunner().invoke(_app(), arguments, env=environment).exit_code == 0
    assert CliRunner().invoke(_app(), arguments, env=environment).exit_code == 0
    assert preflight == [(["sale", "stock", "account"], ["project", "crm", "helpdesk"])] * 2
    assert reconciled == preflight


def test_restored_marker_rejects_selected_after_restore_hook_without_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "pre.py"
    script.write_text("# native pre-upgrade\n", encoding="utf-8")
    hook_dir = tmp_path / "hooks"
    hook_dir.mkdir()
    (hook_dir / "restore.py").write_text(
        "from godoo_cli.runtime.hooks import Hook\n"
        "HOOK = Hook(id='restore', phase='after_restore', on=('restored',))\n"
        "def run(ctx): pass\n",
        encoding="utf-8",
    )
    config = _config(tmp_path)
    write_runtime_lifecycle(
        runtime_readiness_marker(config.data_dir, config.db_name), config.db_name, outcome="restored"
    )
    monkeypatch.setattr(runtime_init, "resolve_command_config", lambda **_kwargs: config)
    monkeypatch.setattr(runtime_init, "require_odoo_version", lambda *_args: SimpleNamespace(major=19, raw="19.0"))
    monkeypatch.setattr(
        runtime_init, "deployment_init", lambda *_args, **_kwargs: pytest.fail("must not mutate runtime")
    )

    result = CliRunner().invoke(
        _app(),
        ["--update", "sale", "--pre-upgrade-script", str(script), "--hooks-dir", str(hook_dir)],
        env=_environment(tmp_path),
    )

    assert result.exit_code == 2, result.output
    assert "after_restore hook" in result.output


def test_environment_excluded_after_restore_hook_allows_native_pre_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "pre.py"
    script.write_text("# native pre-upgrade\n", encoding="utf-8")
    hook_dir = tmp_path / "hooks"
    hook_dir.mkdir()
    (hook_dir / "restore.py").write_text(
        "from godoo_cli.runtime.hooks import Hook\n"
        "HOOK = Hook(id='restore', phase='after_restore', on=('restored',), environments=('development',))\n"
        "def run(ctx): pass\n",
        encoding="utf-8",
    )
    config = _config(tmp_path)
    write_runtime_lifecycle(
        runtime_readiness_marker(config.data_dir, config.db_name), config.db_name, outcome="restored"
    )
    monkeypatch.setattr(runtime_init, "resolve_command_config", lambda **_kwargs: config)
    monkeypatch.setattr(runtime_init, "require_odoo_version", lambda *_args: SimpleNamespace(major=19, raw="19.0"))
    monkeypatch.setattr(runtime_init, "x_sendfile_enabled", lambda *_args: False)
    monkeypatch.setattr(runtime_init, "deployment_init", lambda *_args, **_kwargs: (LifecycleOutcome.READY, 0))

    result = CliRunner().invoke(
        _app(),
        ["--update", "sale", "--pre-upgrade-script", str(script), "--hooks-dir", str(hook_dir)],
        env=_environment(tmp_path) | {"GODOO_ENVIRONMENT": "production"},
    )

    assert result.exit_code == 0, result.output
