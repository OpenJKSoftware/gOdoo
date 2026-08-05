from pathlib import Path

from godoo_cli.devcontainer_hooks import run_devcontainer_post_bootstrap_hooks


def test_hooks_run_report_url_only_outside_staging(tmp_path: Path):
    actions: list[str] = []

    result = run_devcontainer_post_bootstrap_hooks(
        staging=False,
        set_dev_password=False,
        migrations_dir=tmp_path / "missing",
        set_report_url=lambda: actions.append("report-url") or 0,
        set_all_user_passwords=lambda: actions.append("passwords") or 0,
        set_admin_login=lambda: actions.append("login") or 0,
        run_migration=lambda path: actions.append(path.name) or 0,
    )

    assert result == 0
    assert actions == ["report-url"]


def test_staging_hooks_run_in_order_and_stop_at_failure(tmp_path: Path):
    migrations = tmp_path / "staging"
    migrations.mkdir()
    (migrations / "20_second.py").touch()
    (migrations / "10_first.py").touch()
    actions: list[str] = []

    result = run_devcontainer_post_bootstrap_hooks(
        staging=True,
        set_dev_password=True,
        migrations_dir=migrations,
        set_report_url=lambda: actions.append("report-url") or 0,
        set_all_user_passwords=lambda: actions.append("passwords") or 0,
        set_admin_login=lambda: actions.append("login") or 0,
        run_migration=lambda path: actions.append(path.name) or (9 if path.name == "10_first.py" else 0),
    )

    assert result == 9
    assert actions == ["report-url", "passwords", "login", "10_first.py"]
