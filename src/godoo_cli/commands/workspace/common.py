"""Workspace CLI adapters."""

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv

from ...workspace.types import WorkspaceError, WorkspaceSettings, validate_sources_root

LOGGER = logging.getLogger(__name__)


def _sources_root(sources_root: Path | None) -> Path:
    """Resolve the shared source root from CLI input and the project environment."""
    project_root = Path.cwd()
    load_dotenv(project_root / ".env", override=False)
    if sources_root is None and (configured_root := os.environ.get("GODOO_SOURCES_ROOT")):
        sources_root = Path(configured_root)
    if sources_root is None:
        message = "Required environment variable is missing: GODOO_SOURCES_ROOT"
        raise typer.BadParameter(message)
    return validate_sources_root(project_root, sources_root)


def _settings(
    manifest_path: Path,
    sources_root: Path | None,
    debug_port: int,
) -> WorkspaceSettings:
    """Resolve one CLI invocation from its current project directory."""
    return WorkspaceSettings.create(
        project_root=Path.cwd(),
        manifest_path=manifest_path,
        sources_root=_sources_root(sources_root),
        debug_port=debug_port,
    )


def _run(
    action: str,
    operation: Callable[[], dict[str, Any]],
    *,
    json_output: bool,
    text: Callable[[dict[str, Any]], None],
) -> None:
    """Translate workspace domain failures and render one command result."""
    try:
        result = operation()
    except WorkspaceError as error:
        LOGGER.debug("Workspace %s failed: %s", action, error)
        typer.echo(f"Workspace {action} failed: {error}", err=True)
        raise typer.Exit(1) from None
    if json_output:
        typer.echo(json.dumps(result, sort_keys=True))
    else:
        text(result)
