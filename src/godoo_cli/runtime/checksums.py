"""Select native module upgrades from registry-free source snapshots."""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
from collections.abc import Container, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..helpers.hashing import fingerprint, sha256_file
from ..models import GodooConfig, GodooModules

LOGGER = logging.getLogger(__name__)
BASELINE_KEY = "godoo.runtime.source_checksums.v1"
ACTIVE_STATES = {"installed", "to upgrade", "to install", "to remove"}
EXCLUDED_DIRECTORIES = {".git", "__pycache__", "tests", "static"}
EXCLUDED_FILES = ("*.pyc", "*.pyo", "*.swp")


@dataclass(frozen=True)
class ModuleState:
    """Describe module state without loading an Odoo registry."""

    state: str
    imported: bool = False


def module_states(config: GodooConfig) -> dict[str, ModuleState]:
    """Read module states, including optional database-only imported addons."""
    with config.db_connection.with_db(config.db_name, readonly=True).connect() as cursor:
        cursor.execute(
            "SELECT name, state, COALESCE((to_jsonb(module)->>'imported')::boolean, false) "
            "FROM ir_module_module AS module"
        )
        return {name: ModuleState(state, imported) for name, state, imported in cursor.fetchall()}


def pending_installs(states: dict[str, ModuleState], requested: list[str]) -> list[str]:
    """Keep desired installs that are not already installed or queued for upgrade."""
    return list(
        dict.fromkeys(
            name
            for name in requested
            if states.get(name, ModuleState("unknown")).state not in {"installed", "to upgrade"}
        )
    )


def _tree_hash(path: Path, *, core: bool = False) -> str:
    files = []
    for directory, folders, names in os.walk(path):
        folders[:] = sorted(
            name
            for name in folders
            if name not in EXCLUDED_DIRECTORIES and not (core and Path(directory) == path and name == "addons")
        )
        for name in sorted(names):
            if any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_FILES):
                continue
            source = Path(directory) / name
            files.append((source.relative_to(path).as_posix(), sha256_file(source)))
    return fingerprint(files)


def source_snapshot(
    config: GodooConfig, states: dict[str, ModuleState], requested: Sequence[str] = ()
) -> dict[str, str]:
    """Capture active/requested sources and their possible native install dependencies.

    All translations are included, independently of database language settings.
    Auto-install candidates are captured only when the dependency set can introduce installs;
    native Odoo remains responsible for deciding which candidates actually install.

    Returns:
        Candidate module names mapped to deterministic source digests.
    """
    registry = GodooModules(list(config.addon_paths))
    available = {module.name: module for module in registry.get_modules()}
    roots = _required_sources(states, available) | set(requested)
    if "all" in requested:
        roots.update(available)
    selected = [available[name] for name in sorted(roots & available.keys())]
    selected.extend(registry.get_module_dependencies(selected))
    if any(
        states.get(module.name, ModuleState("unknown")).state not in {"installed", "to upgrade"} for module in selected
    ):
        automatic = [module for module in available.values() if module.manifest.get("auto_install", False) is not False]
        selected.extend(automatic)
        selected.extend(registry.get_module_dependencies(automatic))
    result = {module.name: _tree_hash(module.path) for module in dict.fromkeys(selected)}
    if "base" in result:
        result["base"] = fingerprint([result["base"], _tree_hash(config.odoo_install_folder / "odoo", core=True)])
    LOGGER.debug("Source snapshot covers %s of %s available addons", len(result), len(available))
    return result


def _baseline(config: GodooConfig) -> dict[str, str] | None:
    with config.db_connection.with_db(config.db_name, readonly=True).connect() as cursor:
        cursor.execute("SELECT value FROM ir_config_parameter WHERE key = %s", [BASELINE_KEY])
        row = cursor.fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, ValueError) as error:
        message = "The gOdoo source checksum baseline is corrupt."
        raise RuntimeError(message) from error
    if (
        not isinstance(value, dict)
        or type(value.get("format")) is not int
        or value.get("format") != 1
        or not isinstance(value.get("modules"), dict)
        or any(
            not isinstance(name, str) or not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            for name, checksum in value["modules"].items()
        )
    ):
        message = "The gOdoo source checksum baseline has an unsupported format."
        raise RuntimeError(message)
    return value["modules"]


def _required_sources(states: dict[str, ModuleState], snapshot: Container[str]) -> set[str]:
    return {
        name
        for name, module in states.items()
        if module.state in ACTIVE_STATES
        and not module.imported
        and not (name == "studio_customization" and name not in snapshot)
    }


@dataclass(frozen=True)
class ModulePlan:
    """Hold the pre-native snapshot and the native selections it requires."""

    updates: list[str]
    installs: list[str]
    snapshot: dict[str, str]

    def finish(self, config: GodooConfig) -> None:
        """Verify native completion and unchanged sources before saving this snapshot."""
        states = module_states(config)
        pending = sorted(name for name, module in states.items() if module.state.startswith("to "))
        if pending:
            message = f"Native reconciliation left unfinished modules: {', '.join(pending)}"
            raise RuntimeError(message)
        missing_installs = sorted(
            name
            for name in self.installs
            if name != "all" and states.get(name, ModuleState("unknown")).state != "installed"
        )
        if missing_installs:
            message = f"Requested modules were not installed: {', '.join(missing_installs)}"
            raise RuntimeError(message)
        current = source_snapshot(config, states)
        required = _required_sources(states, current)
        missing = required - current.keys()
        if missing:
            message = f"Installed module sources remain unavailable after reconciliation: {', '.join(sorted(missing))}"
            raise RuntimeError(message)
        changed = sorted(name for name in required if self.snapshot.get(name) != current[name])
        if changed:
            message = f"Module sources changed during reconciliation: {', '.join(changed)}; retry initialization."
            raise RuntimeError(message)
        baseline = {"format": 1, "modules": {name: self.snapshot[name] for name in sorted(required)}}
        with config.db_connection.connect() as cursor:
            cursor.execute(
                "INSERT INTO ir_config_parameter (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                [BASELINE_KEY, json.dumps(baseline, sort_keys=True)],
            )
        LOGGER.info("Saved source checksum baseline for %s installed modules", len(required))


def plan_module_changes(
    config: GodooConfig, updates: list[str], installs: list[str], *, pre_upgrade_scripts: bool = False
) -> ModulePlan:
    """Plan changed and pending modules, delegating dependency expansion to native Odoo."""
    states = module_states(config)
    snapshot = source_snapshot(config, states, [*updates, *installs])
    previous = _baseline(config)
    required = _required_sources(states, snapshot)
    available = required & snapshot.keys()
    selected_updates = list(
        dict.fromkeys(
            [
                *updates,
                *sorted(
                    name
                    for name in available
                    if states[name].state in {"installed", "to upgrade"}
                    and (states[name].state == "to upgrade" or previous is None or previous.get(name) != snapshot[name])
                ),
            ]
        )
    )
    selected_installs = pending_installs(
        states, [*installs, *sorted(name for name in available if states[name].state == "to install")]
    )
    missing = required - snapshot.keys()
    if missing and not (pre_upgrade_scripts and selected_updates):
        message = f"Installed module sources are unavailable: {', '.join(sorted(missing))}"
        raise RuntimeError(message)
    if missing:
        LOGGER.info(
            "Deferring missing installed sources to selected native pre-upgrade cleanup: %s", ", ".join(sorted(missing))
        )
    removals = sorted(name for name, module in states.items() if module.state == "to remove")
    if removals and not (selected_updates or selected_installs):
        message = (
            f"Modules are pending removal; select an explicit native update to complete them: {', '.join(removals)}"
        )
        raise RuntimeError(message)
    LOGGER.info("Source reconciliation selected updates=%s installs=%s", selected_updates, selected_installs)
    return ModulePlan(selected_updates, selected_installs, snapshot)
