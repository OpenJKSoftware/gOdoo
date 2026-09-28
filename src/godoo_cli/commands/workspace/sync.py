"""Workspace CLI adapters."""

from pathlib import Path
from typing import Annotated

import typer
from rich import print as rich_print

from ...workspace import check_workspace, sync_workspace
from ..common import WorkspaceCLIArgs
from .common import _run, _settings


def workspace_sync(
    manifest_path: Annotated[Path, WorkspaceCLIArgs.manifest_path] = Path("odoo_manifest.yml"),
    sources_root: Annotated[Path | None, WorkspaceCLIArgs.sources_root] = None,
    debug_port: Annotated[int, WorkspaceCLIArgs.debug_port] = 5678,
    json_output: Annotated[bool, WorkspaceCLIArgs.json_output] = False,
) -> None:
    """Reset managed worktrees to manifest refs and update local state."""
    _run(
        "synchronized",
        lambda: sync_workspace(_settings(manifest_path, sources_root, debug_port)),
        json_output=json_output,
        text=lambda result: rich_print(f"Workspace synchronized at [bold]{result['project_root']}[/bold]"),
    )


def workspace_check(
    manifest_path: Annotated[Path, WorkspaceCLIArgs.manifest_path] = Path("odoo_manifest.yml"),
    sources_root: Annotated[Path | None, WorkspaceCLIArgs.sources_root] = None,
    debug_port: Annotated[int, WorkspaceCLIArgs.debug_port] = 5678,
    json_output: Annotated[bool, WorkspaceCLIArgs.json_output] = False,
    sources_only: Annotated[
        bool,
        typer.Option(
            "--sources-only",
            help="Check selected sources and archives without checking generated editor files.",
        ),
    ] = False,
) -> None:
    """Check sources offline, optionally skipping generated editor files."""
    _run(
        "is current",
        lambda: check_workspace(_settings(manifest_path, sources_root, debug_port), sources_only=sources_only),
        json_output=json_output,
        text=lambda result: rich_print(
            f"Workspace sources are current at [bold]{result['project_root']}[/bold]"
            if sources_only
            else f"Workspace is current at [bold]{result['project_root']}[/bold]"
        ),
    )
