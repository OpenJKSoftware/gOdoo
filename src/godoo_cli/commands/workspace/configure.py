"""Workspace CLI adapters."""

import logging
import shlex
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rich_print

from ...workspace import configure_workspace, runtime_environment
from ...workspace.types import WorkspaceError
from ..common import WorkspaceCLIArgs
from .common import _run, _settings

LOGGER = logging.getLogger(__name__)


def workspace_configure(
    manifest_path: Annotated[Path, WorkspaceCLIArgs.manifest_path] = Path("odoo_manifest.yml"),
    sources_root: Annotated[Path | None, WorkspaceCLIArgs.sources_root] = None,
    debug_port: Annotated[int, WorkspaceCLIArgs.debug_port] = 5678,
    json_output: Annotated[bool, WorkspaceCLIArgs.json_output] = False,
) -> None:
    """Regenerate the VS Code workspace from checked sources."""
    _run(
        "configured",
        lambda: configure_workspace(_settings(manifest_path, sources_root, debug_port)),
        json_output=json_output,
        text=lambda result: rich_print(f"Workspace configured at [bold]{result['project_root']}[/bold]"),
    )


def workspace_runtime_env(
    manifest_path: Annotated[Path, WorkspaceCLIArgs.manifest_path] = Path("odoo_manifest.yml"),
    sources_root: Annotated[Path | None, WorkspaceCLIArgs.sources_root] = None,
    debug_port: Annotated[int, WorkspaceCLIArgs.debug_port] = 5678,
) -> None:
    """Print shell-safe host-resolved paths for a development runtime."""
    try:
        environment = runtime_environment(_settings(manifest_path, sources_root, debug_port))
    except WorkspaceError as error:
        LOGGER.debug("Workspace runtime environment failed: %s", error)
        typer.echo(f"Workspace runtime environment failed: {error}", err=True)
        raise typer.Exit(1) from error
    for name, value in environment.items():
        typer.echo(f"{name}={shlex.quote(value)}")
