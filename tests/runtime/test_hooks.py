"""Focused tests for project lifecycle hooks."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Self

import pytest

from godoo_cli.models import GodooConfig
from godoo_cli.runtime import hooks


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


def _hook(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("from godoo_cli.runtime.hooks import Hook\n" + body, encoding="utf-8")


def test_discover_hooks_orders_files_and_rejects_duplicate_ids(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _hook(first / "20_second.py", "HOOK = Hook(id='second', phase='prepare')\ndef run(ctx): pass\n")
    _hook(first / "10_first.py", "HOOK = Hook(id='first', phase='prepare')\ndef run(ctx): pass\n")
    _hook(second / "duplicate.py", "HOOK = Hook(id='first', phase='prepare')\ndef run(ctx): pass\n")
    assert [definition.hook.id for definition in hooks.discover_hooks([first])] == ["first", "second"]
    with pytest.raises(ValueError, match="must be a directory"):
        hooks.discover_hooks([first / "20_second.py"])
    with pytest.raises(ValueError, match="Duplicate hook id 'first'"):
        hooks.discover_hooks([first, second])


def test_prepare_hooks_merge_requests_and_never_expose_env(tmp_path: Path) -> None:
    source = tmp_path / "hooks" / "prepare.py"
    _hook(
        source,
        "HOOK = Hook(id='prepare', phase='prepare')\n"
        "def run(ctx):\n"
        "    assert not hasattr(ctx, 'env')\n"
        "    ctx.request_modules(update=['sale', 'stock', 'sale'], install=['web', 'web'])\n",
    )
    definitions = hooks.discover_hooks([source.parent])
    assert hooks.run_prepare_hooks(definitions, "development") == hooks.ModuleSelections(
        update=("sale", "stock"), install=("web",)
    )


def test_database_phase_filters_before_starting_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "hooks" / "restore.py"
    _hook(
        source,
        "HOOK = Hook(id='restore', phase='after_restore', on=('restored',), environments=('development',))\n"
        "def run(ctx): pass\n",
    )
    definitions = hooks.discover_hooks([source.parent])
    monkeypatch.setattr(hooks, "build_odoo_shell_command", lambda _config: pytest.fail("shell should not start"))
    assert hooks.run_hook_phase(_config(tmp_path), definitions, "after_restore", "ready", None) == 0
    assert not hooks.has_eligible_hooks(definitions, phase="after_restore", outcome="ready", environment=None)


def test_database_phase_invokes_one_shell_with_upgrade_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "hooks" / "reconcile.py"
    _hook(source, "HOOK = Hook(id='reconcile', phase='after_reconcile')\ndef run(ctx): pass\n")
    _hook(source.parent / "second.py", "HOOK = Hook(id='second', phase='after_reconcile')\ndef run(ctx): pass\n")
    definitions = hooks.discover_hooks([source.parent])
    calls: list[tuple[list[str], str]] = []
    monkeypatch.setattr(hooks, "build_odoo_shell_command", lambda _config: ["odoo-bin", "shell"])
    monkeypatch.setattr(
        hooks,
        "run_odoo_command",
        lambda command, **kwargs: calls.append((command, kwargs["input"])) or SimpleNamespace(returncode=0),
    )
    assert (
        hooks.run_hook_phase(
            _config(tmp_path),
            definitions,
            "after_reconcile",
            "restored",
            "development",
            upgrade_path=[tmp_path / "migrations"],
        )
        == 0
    )
    assert calls[0][0][-2:] == ["--upgrade-path", str((tmp_path / "migrations").resolve())]
    assert "_run_database_hook_batch" in calls[0][1]
    assert calls[0][1].count(".py") == 2


def test_discovery_rejects_invalid_callback_before_execution(tmp_path: Path) -> None:
    source = tmp_path / "hooks" / "invalid.py"
    _hook(source, "HOOK = Hook(id='invalid', phase='prepare')\ndef run(): pass\n")
    with pytest.raises(TypeError, match="must accept one context argument"):
        hooks.discover_hooks([source.parent])


def test_prepare_predicate_must_return_a_real_bool(tmp_path: Path) -> None:
    source = tmp_path / "hooks" / "predicate.py"
    _hook(
        source,
        "HOOK = Hook(id='predicate', phase='prepare')\n"
        "def needs_run(ctx): return 1\n"
        "def run(ctx): raise AssertionError('must not run')\n",
    )
    with pytest.raises(TypeError, match="must return bool"):
        hooks.run_prepare_hooks(hooks.discover_hooks([source.parent]), environment=None)


def test_database_batch_uses_fresh_cursor_and_rolls_back_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "first" / "50_skip.py"
    _hook(
        source,
        "import builtins\n"
        "HOOK = Hook(id='skip', phase='after_reconcile')\n"
        "def needs_run(ctx):\n"
        "    builtins.hook_events.append(('needs', ctx.env.cr.number))\n"
        "    return False\n"
        "def run(ctx): raise AssertionError('skip must not run')\n",
    )
    second = tmp_path / "second" / "10_run.py"
    _hook(
        second,
        "import builtins\n"
        "HOOK = Hook(id='run', phase='after_reconcile')\n"
        "def run(ctx): builtins.hook_events.append(('run', ctx.env.cr.number))\n",
    )

    class Cursor:
        def __init__(self, number: int) -> None:
            self.number = number
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

    class Registry:
        def __init__(self) -> None:
            self.cursors: list[Cursor] = []

        def cursor(self) -> Cursor:
            cursor = Cursor(len(self.cursors) + 1)
            self.cursors.append(cursor)
            return cursor

    events: list[tuple[str, int]] = []

    class Environment:
        def __init__(self, cursor: Cursor, _uid: int, _context: dict[str, object]) -> None:
            self.cr = cursor

        def flush_all(self) -> None:
            events.append(("flush", self.cr.number))

    odoo = ModuleType("odoo")
    api: Any = ModuleType("odoo.api")
    api.Environment = Environment
    monkeypatch.setitem(sys.modules, "odoo", odoo)
    monkeypatch.setitem(sys.modules, "odoo.api", api)
    registry = Registry()
    monkeypatch.setattr(builtins, "hook_events", events, raising=False)
    hooks._run_database_hook_batch(
        SimpleNamespace(registry=registry, uid=3, context={}),
        [str(source), str(second)],
        outcome="restored",
        environment=None,
    )
    assert events == [("needs", 1), ("run", 2), ("flush", 2)]
    assert [(cursor.rollbacks, cursor.commits) for cursor in registry.cursors] == [(1, 0), (0, 1)]


def test_database_batch_rolls_back_and_stops_after_action_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "hooks" / "broken.py"
    _hook(
        source,
        "HOOK = Hook(id='broken', phase='after_reconcile')\n"
        "def run(ctx):\n"
        "    ctx.env.cr.commit()\n"
        "    raise RuntimeError('broken hook')\n",
    )

    class Cursor:
        commits = 0
        rollbacks = 0

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

    cursor = Cursor()
    registry = SimpleNamespace(cursor=lambda: cursor)

    class Environment:
        def __init__(self, current: Cursor, _uid: int, _context: dict[str, object]) -> None:
            self.cr = current

        def flush_all(self) -> None:
            pytest.fail("failed actions must not flush")

    odoo = ModuleType("odoo")
    api: Any = ModuleType("odoo.api")
    api.Environment = Environment
    monkeypatch.setitem(sys.modules, "odoo", odoo)
    monkeypatch.setitem(sys.modules, "odoo.api", api)
    with pytest.raises(RuntimeError, match="broken hook"):
        hooks._run_database_hook_batch(
            SimpleNamespace(registry=registry, uid=3, context={}), [str(source)], outcome="restored", environment=None
        )
    assert (cursor.commits, cursor.rollbacks) == (1, 1)
