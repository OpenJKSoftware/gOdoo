"""Source snapshots select native work without certifying unreconciled code."""

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from godoo_cli.models import GodooConfig
from godoo_cli.runtime import checksums
from godoo_cli.runtime.checksums import ModuleState


class Database:
    """Represent the SQL boundary used by the registry-free planner."""

    def __init__(self) -> None:
        self.states = {"base": ModuleState("installed"), "custom": ModuleState("installed")}
        self.baseline: str | None = None
        self.query = ""

    def with_db(self, *_args: object, **_kwargs: object) -> "Database":
        return self

    @contextmanager
    def connect(self):
        yield self

    def execute(self, query: str, parameters: Any = None) -> None:
        self.query = query
        if query.startswith("INSERT"):
            assert parameters[0] == checksums.BASELINE_KEY
            self.baseline = parameters[1]

    def fetchall(self):
        return [(name, module.state, module.imported) for name, module in self.states.items()]

    def fetchone(self):
        return (self.baseline,) if self.baseline is not None else None


def _module(root: Path, name: str, depends: tuple[str, ...] = ()) -> Path:
    source = root / name
    source.mkdir(parents=True)
    (source / "__manifest__.py").write_text(repr({"name": name, "version": "19.0.1", "depends": list(depends)}))
    (source / "__init__.py").write_text("")
    return source


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[GodooConfig, Database, Path]:
    addons = tmp_path / "addons"
    _module(addons, "base")
    _module(addons, "custom", ("base",))
    config = GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=addons,
        thirdparty_addon_path=tmp_path / "thirdparty",
        resolved_addon_paths=(addons,),
        db_name="runtime",
    )
    (config.odoo_install_folder / "odoo").mkdir(parents=True)
    database = Database()
    monkeypatch.setattr(GodooConfig, "db_connection", property(lambda _self: database))
    return config, database, addons


def test_first_reconcile_then_unchanged_and_desired_installs(runtime: tuple[GodooConfig, Database, Path]) -> None:
    config, database, addons = runtime
    _module(addons, "new_feature", ("custom",))
    plan = checksums.plan_module_changes(config, [], ["new_feature", "custom"])
    assert plan.updates == ["base", "custom"]
    assert plan.installs == ["new_feature"]
    assert database.baseline is None
    database.states["new_feature"] = ModuleState("installed")
    plan.finish(config)
    saved = json.loads(database.baseline)
    assert set(saved["modules"]) == {"base", "custom", "new_feature"}
    again = checksums.plan_module_changes(config, [], ["new_feature", "custom"])
    assert (again.updates, again.installs) == ([], [])


def test_core_translations_renames_and_exclusions(runtime: tuple[GodooConfig, Database, Path]) -> None:
    config, database, addons = runtime
    checksums.plan_module_changes(config, [], []).finish(config)
    (addons / "custom" / "tests").mkdir()
    (addons / "custom" / "tests" / "ignored.py").write_text("test change")
    assert checksums.plan_module_changes(config, [], []).updates == []
    translation = addons / "custom" / "i18n"
    translation.mkdir()
    (translation / "fr.po").write_text("translation")
    assert checksums.plan_module_changes(config, [], []).updates == ["custom"]
    checksums.plan_module_changes(config, [], []).finish(config)
    (translation / "fr.po").rename(translation / "de.po")
    assert checksums.plan_module_changes(config, [], []).updates == ["custom"]
    (config.odoo_install_folder / "odoo" / "api.py").write_text("core evolution")
    assert checksums.plan_module_changes(config, ["custom"], []).updates == ["custom", "base"]
    assert database.baseline is not None


def test_pending_states_are_not_hidden_by_matching_hashes(runtime: tuple[GodooConfig, Database, Path]) -> None:
    config, database, addons = runtime
    checksums.plan_module_changes(config, [], []).finish(config)
    _module(addons, "dependency")
    database.states["custom"] = ModuleState("to upgrade")
    database.states["dependency"] = ModuleState("to install")
    plan = checksums.plan_module_changes(config, [], [])
    assert plan.updates == ["custom"]
    assert plan.installs == ["dependency"]
    old = database.baseline
    with pytest.raises(RuntimeError, match="unfinished modules"):
        plan.finish(config)
    assert database.baseline == old
    database.states["custom"] = ModuleState("installed")
    database.states["dependency"] = ModuleState("to remove")
    with pytest.raises(RuntimeError, match="pending removal"):
        checksums.plan_module_changes(config, [], [])


@pytest.mark.parametrize("cleanup_removes_missing", [False, True])
def test_missing_sources_require_real_cleanup_and_strict_rescan(
    runtime: tuple[GodooConfig, Database, Path], cleanup_removes_missing: bool
) -> None:
    config, database, _addons = runtime
    checksums.plan_module_changes(config, [], []).finish(config)
    old = database.baseline
    database.states["obsolete"] = ModuleState("installed")
    with pytest.raises(RuntimeError, match="sources are unavailable"):
        checksums.plan_module_changes(config, [], [], pre_upgrade_scripts=True)
    plan = checksums.plan_module_changes(config, ["base"], [], pre_upgrade_scripts=True)
    if cleanup_removes_missing:
        del database.states["obsolete"]
        plan.finish(config)
        assert "obsolete" not in json.loads(database.baseline)["modules"]
    else:
        with pytest.raises(RuntimeError, match="remain unavailable"):
            plan.finish(config)
        assert database.baseline == old


def test_imported_and_pathless_studio_are_not_source_modules(runtime: tuple[GodooConfig, Database, Path]) -> None:
    config, database, _addons = runtime
    database.states.update(imported=ModuleState("installed", True), studio_customization=ModuleState("installed"))
    plan = checksums.plan_module_changes(config, [], [])
    assert plan.updates == ["base", "custom"]
    plan.finish(config)
    assert set(json.loads(database.baseline)["modules"]) == {"base", "custom"}


def test_source_drift_and_corrupt_baseline_never_get_certified(runtime: tuple[GodooConfig, Database, Path]) -> None:
    config, database, addons = runtime
    plan = checksums.plan_module_changes(config, [], [])
    (addons / "custom" / "__init__.py").write_text("changed during native work")
    with pytest.raises(RuntimeError, match="sources changed during reconciliation"):
        plan.finish(config)
    assert database.baseline is None
    database.baseline = "broken json"
    with pytest.raises(RuntimeError, match="corrupt"):
        checksums.plan_module_changes(config, [], [])
    database.baseline = '{"format": 2, "modules": {}}'
    with pytest.raises(RuntimeError, match="unsupported format"):
        checksums.plan_module_changes(config, [], [])


def test_snapshot_scope_excludes_unrelated_addons_but_captures_new_dependencies(
    runtime: tuple[GodooConfig, Database, Path],
) -> None:
    config, _database, addons = runtime
    _module(addons, "unrelated")
    dependency = _module(addons, "new_dependency")
    automatic = _module(addons, "automatic_bridge", ("new_dependency",))
    (automatic / "__manifest__.py").write_text(
        repr({"name": "automatic_bridge", "version": "19.0.1", "depends": ["new_dependency"], "auto_install": True})
    )
    first = checksums.plan_module_changes(config, [], [])
    assert set(first.snapshot) == {"base", "custom"}
    (addons / "custom" / "__manifest__.py").write_text(
        repr({"name": "custom", "version": "19.0.1", "depends": ["base", dependency.name]})
    )
    changed = checksums.plan_module_changes(config, [], [])
    assert set(changed.snapshot) == {"base", "custom", "new_dependency", "automatic_bridge"}
    assert "unrelated" in checksums.plan_module_changes(config, [], ["all"]).snapshot
