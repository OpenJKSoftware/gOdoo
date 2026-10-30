"""Discover and run project lifecycle hooks."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import logging
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from ..models import GodooConfig
from .odoo import build_odoo_shell_command, run_odoo_command

LOGGER = logging.getLogger(__name__)

PHASES = ("prepare", "after_bootstrap", "after_restore", "after_reconcile")
OUTCOMES = ("ready", "bootstrapped", "restored")


@dataclass(frozen=True)
class Hook:
    """Describe one project-owned lifecycle hook."""

    id: str
    phase: str
    on: tuple[str, ...] = OUTCOMES
    environments: tuple[str, ...] | None = None
    once: bool = False

    def __post_init__(self) -> None:
        """Reject declarations that cannot be evaluated safely."""
        if not isinstance(self.id, str) or not self.id.strip():
            message = "Hook id must be a nonempty string."
            raise ValueError(message)
        if self.phase not in PHASES:
            message = f"Unknown hook phase '{self.phase}'."
            raise ValueError(message)
        _validate_string_tuple("Hook outcomes", self.on, allowed=OUTCOMES)
        if type(self.once) is not bool:
            message = "Hook once must be a boolean."
            raise ValueError(message)
        if self.phase == "prepare" and self.once:
            message = "Preparation hooks cannot declare once=True."
            raise ValueError(message)
        if self.phase == "prepare" and self.on != OUTCOMES:
            message = "Preparation hooks cannot filter by outcome."
            raise ValueError(message)
        if self.environments is not None:
            _validate_string_tuple("Hook environments", self.environments)


@dataclass(frozen=True)
class HookDefinition:
    """An imported hook declaration and its executable callbacks."""

    hook: Hook
    source: Path
    run: Callable[[Any], Any]
    needs_run: Callable[[Any], Any] | None = None


@dataclass(frozen=True)
class ModuleSelections:
    """Additive module selections collected from preparation hooks."""

    update: tuple[str, ...] = ()
    install: tuple[str, ...] = ()


@dataclass
class _PrepareContext:
    environment: str | None
    _update: list[str] = field(default_factory=list)
    _install: list[str] = field(default_factory=list)

    def request_modules(self, *, update: Sequence[str] = (), install: Sequence[str] = ()) -> None:
        """Add explicit module selections in first-requested order."""
        self._update.extend(_module_names(update, "update"))
        self._install.extend(_module_names(install, "install"))


@dataclass(frozen=True)
class _DatabaseContext:
    env: Any
    environment: str | None
    outcome: str


def discover_hooks(hook_directories: Iterable[Path]) -> list[HookDefinition]:
    """Import hook files in stable order and validate their declarations."""
    return _discover_hook_sources(hook_directories, allow_files=False)


def _discover_hook_sources(sources: Iterable[Path], *, allow_files: bool) -> list[HookDefinition]:
    """Load resolved hook sources, allowing direct files only inside Odoo shell batches."""
    definitions: list[HookDefinition] = []
    seen_sources: set[Path] = set()
    seen_inputs: set[Path] = set()
    hook_ids: set[str] = set()
    for supplied in sources:
        source = Path(supplied).expanduser().resolve()
        if source in seen_inputs:
            continue
        seen_inputs.add(source)
        if not source.is_dir() and not allow_files:
            message = f"Hook path '{source}' must be a directory."
            raise ValueError(message)
        candidates = _hook_files(source)
        for candidate in candidates:
            if candidate in seen_sources:
                continue
            seen_sources.add(candidate)
            definition = _load_hook(candidate)
            if definition.hook.id in hook_ids:
                message = f"Duplicate hook id '{definition.hook.id}'."
                raise ValueError(message)
            hook_ids.add(definition.hook.id)
            definitions.append(definition)
    return definitions


def has_eligible_hooks(hooks: Iterable[HookDefinition], *, phase: str, outcome: str, environment: str | None) -> bool:
    """Return whether a database or preparation phase has work to perform."""
    _validate_phase_and_outcome(phase, outcome)
    return any(_is_eligible(definition.hook, phase, outcome, environment) for definition in hooks)


def run_prepare_hooks(hooks: Iterable[HookDefinition], environment: str | None) -> ModuleSelections:
    """Run eligible preparation hooks and collect their module requests."""
    context = _PrepareContext(environment=environment)
    for definition in hooks:
        if not _is_eligible(definition.hook, "prepare", None, environment):
            LOGGER.info("Hook %s excluded by declaration.", definition.hook.id)
            continue
        _run_prepare_hook(definition, context)
    return ModuleSelections(update=_deduplicate(context._update), install=_deduplicate(context._install))


def run_hook_phase(
    config: GodooConfig,
    hooks: Iterable[HookDefinition],
    phase: str,
    outcome: str,
    environment: str | None,
    upgrade_path: list[Path] | Path | None = None,
) -> int:
    """Run one eligible database phase in a single Odoo shell subprocess."""
    _validate_phase_and_outcome(phase, outcome)
    if phase == "prepare":
        message = "Preparation hooks run in the host process."
        raise ValueError(message)
    definitions = list(hooks)
    selected = [definition for definition in definitions if _is_eligible(definition.hook, phase, outcome, environment)]
    for definition in definitions:
        if definition not in selected:
            LOGGER.info("Hook %s excluded by declaration.", definition.hook.id)
    if not selected:
        return 0

    command = build_odoo_shell_command(config)
    paths = _normalize_upgrade_paths(upgrade_path)
    if paths:
        command.extend(["--upgrade-path", ",".join(str(path) for path in paths)])
    script = _database_batch_script(selected, outcome=outcome, environment=environment)
    return run_odoo_command(command, input=script, text=True).returncode


def _run_database_hook_batch(shell_env: Any, sources: Sequence[str], *, outcome: str, environment: str | None) -> None:
    """Execute one already-filtered database phase inside an Odoo shell."""
    from odoo.api import Environment

    definitions = _discover_hook_sources((Path(source) for source in sources), allow_files=True)
    registry = shell_env.registry
    for definition in definitions:
        with registry.cursor() as cursor:
            context = _DatabaseContext(
                env=Environment(cursor, shell_env.uid, dict(shell_env.context)),
                environment=environment,
                outcome=outcome,
            )
            try:
                completion_key = None
                parameters = None
                if definition.hook.once:
                    scope = environment if definition.hook.environments is not None else None
                    identity = json.dumps([definition.hook.id, scope], separators=(",", ":"))
                    completion_key = "godoo.hooks.once." + hashlib.sha256(identity.encode()).hexdigest()
                    parameters = context.env["ir.config_parameter"].sudo()
                    if parameters.get_param(completion_key):
                        cursor.rollback()
                        LOGGER.info("Hook %s already completed.", definition.hook.id)
                        continue
                if definition.needs_run is not None:
                    decision = definition.needs_run(context)
                    _require_bool(decision, definition.hook.id)
                    if not decision:
                        if parameters is not None:
                            parameters.set_param(completion_key, "1")
                            context.env.flush_all()
                            cursor.commit()
                        else:
                            cursor.rollback()
                        LOGGER.info("Hook %s already satisfied.", definition.hook.id)
                        continue
                LOGGER.info("Hook %s running.", definition.hook.id)
                definition.run(context)
                if parameters is not None:
                    parameters.set_param(completion_key, "1")
                context.env.flush_all()
                cursor.commit()
                LOGGER.info("Hook %s completed.", definition.hook.id)
            except Exception:
                cursor.rollback()
                LOGGER.exception("Hook %s failed.", definition.hook.id)
                raise


def _run_prepare_hook(definition: HookDefinition, context: _PrepareContext) -> None:
    try:
        if definition.needs_run is not None:
            decision = definition.needs_run(context)
            _require_bool(decision, definition.hook.id)
            if not decision:
                LOGGER.info("Hook %s already satisfied.", definition.hook.id)
                return
        LOGGER.info("Hook %s running.", definition.hook.id)
        definition.run(context)
        LOGGER.info("Hook %s completed.", definition.hook.id)
    except Exception:
        LOGGER.exception("Hook %s failed.", definition.hook.id)
        raise


def _hook_files(source: Path) -> list[Path]:
    if source.is_file():
        if source.suffix != ".py":
            message = f"Hook file '{source}' must have a .py suffix."
            raise ValueError(message)
        return [source]
    if not source.is_dir():
        message = f"Hook path '{source}' does not exist or is not a directory."
        raise ValueError(message)
    return [path.resolve() for path in sorted(source.glob("*.py"), key=lambda path: path.name) if path.is_file()]


def _load_hook(source: Path) -> HookDefinition:
    module_name = f"godoo_project_hook_{hashlib.sha256(str(source).encode()).hexdigest()}"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        message = f"Could not load hook file '{source}'."
        raise ValueError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        message = f"Could not import hook file '{source}': {error}"
        raise ValueError(message) from error
    finally:
        sys.modules.pop(module_name, None)
    return _definition_from_module(module, source)


def _definition_from_module(module: ModuleType, source: Path) -> HookDefinition:
    hook = getattr(module, "HOOK", None)
    run = getattr(module, "run", None)
    needs_run = getattr(module, "needs_run", None)
    if not isinstance(hook, Hook):
        message = f"Hook file '{source}' must declare HOOK = Hook(...)."
        raise TypeError(message)
    if not callable(run):
        message = f"Hook file '{source}' must define run(ctx)."
        raise TypeError(message)
    if needs_run is not None and not callable(needs_run):
        message = f"Hook file '{source}' has a non-callable needs_run."
        raise TypeError(message)
    _validate_callback(source, "run", run)
    if needs_run is not None:
        _validate_callback(source, "needs_run", needs_run)
    return HookDefinition(
        hook=hook,
        source=source,
        run=cast(Callable[[Any], Any], run),
        needs_run=cast(Callable[[Any], Any] | None, needs_run),
    )


def _validate_callback(source: Path, name: str, callback: Callable[..., object]) -> None:
    try:
        inspect.signature(callback).bind(object())
    except (TypeError, ValueError) as error:
        message = f"Hook file '{source}' {name} must accept one context argument."
        raise TypeError(message) from error


def _require_bool(value: object, hook_id: str) -> None:
    if type(value) is not bool:
        message = f"Hook {hook_id} needs_run must return bool."
        raise TypeError(message)


def _is_eligible(hook: Hook, phase: str, outcome: str | None, environment: str | None) -> bool:
    if hook.phase != phase:
        return False
    if phase != "prepare" and outcome not in hook.on:
        return False
    return hook.environments is None or (environment is not None and environment in hook.environments)


def _validate_phase_and_outcome(phase: str, outcome: str) -> None:
    if phase not in PHASES:
        message = f"Unknown hook phase '{phase}'."
        raise ValueError(message)
    if outcome not in OUTCOMES:
        message = f"Unknown lifecycle outcome '{outcome}'."
        raise ValueError(message)


def _validate_string_tuple(name: str, values: tuple[str, ...], *, allowed: tuple[str, ...] | None = None) -> None:
    if not isinstance(values, tuple) or not values:
        message = f"{name} must be a nonempty tuple."
        raise TypeError(message)
    if any(not isinstance(value, str) or not value for value in values):
        message = f"{name} must contain nonempty strings."
        raise ValueError(message)
    if len(set(values)) != len(values):
        message = f"{name} must not contain duplicates."
        raise ValueError(message)
    if allowed is not None and any(value not in allowed for value in values):
        message = f"{name} contain an unknown value."
        raise ValueError(message)


def _module_names(values: Sequence[str], request_type: str) -> list[str]:
    if isinstance(values, str) or any(not isinstance(value, str) or not value.strip() for value in values):
        message = f"Requested {request_type} modules must be nonempty strings."
        raise ValueError(message)
    return [value.strip() for value in values]


def _deduplicate(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _normalize_upgrade_paths(values: list[Path] | Path | None) -> list[Path]:
    raw_values = [values] if isinstance(values, Path) else values or []
    paths: list[Path] = []
    seen: set[Path] = set()
    for value in raw_values:
        for item in str(value).split(","):
            if not item.strip():
                continue
            path = Path(item.strip()).expanduser().resolve()
            if path not in seen:
                paths.append(path)
                seen.add(path)
    return paths


def _database_batch_script(hooks: Sequence[HookDefinition], *, outcome: str, environment: str | None) -> str:
    sources = [str(definition.source) for definition in hooks]
    return (
        "from godoo_cli.runtime.hooks import _run_database_hook_batch\n"
        f"_run_database_hook_batch(env, {sources!r}, outcome={outcome!r}, environment={environment!r})\n"
    )
