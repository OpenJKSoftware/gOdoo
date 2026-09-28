"""Workspace CLI adapters."""

import os
from pathlib import Path
from typing import Annotated, Any

import typer
from dotenv import load_dotenv
from rich import print as rich_print

from ...workspace.image import materialize_workspace_image
from ...workspace.types import WorkspaceError, validate_sources_root
from ..common import WorkspaceCLIArgs
from .common import _run


def workspace_materialize(
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            help="Mounted downstream project root.",
        ),
    ] = Path("/build/project"),
    manifest_path: Annotated[
        Path,
        typer.Option(
            "--manifest",
            help="Manifest path, relative to the mounted project root.",
        ),
    ] = Path("/build/project/odoo_manifest.yml"),
    sources_root: Annotated[
        Path,
        typer.Option(
            "--sources-root",
            help="Mounted shared source root.",
        ),
    ] = Path("/build/sources"),
    destination_root: Annotated[
        Path,
        typer.Option(
            "--destination-root",
            help="Image filesystem root receiving canonical /odoo paths.",
        ),
    ] = Path("/"),
    hook_dirs: Annotated[
        list[Path] | None,
        typer.Option(
            "--hook-dir",
            help="Project-relative hook directory; repeat the option for multiple directories.",
        ),
    ] = None,
    json_output: Annotated[bool, WorkspaceCLIArgs.json_output] = False,
) -> None:
    """Copy selected source trees into their canonical production image paths."""

    def operation() -> dict[str, Any]:
        load_dotenv(project_root / ".env", override=False)
        selected_hooks = hook_dirs or []
        selected_sources_root = sources_root
        if not selected_sources_root.is_absolute():
            configured_root = os.environ.get("GODOO_SOURCES_ROOT")
            if configured_root is None:
                message = "Required environment variable missing: GODOO_SOURCES_ROOT"
                raise WorkspaceError(message)
            selected_sources_root = Path(configured_root)
        resolved_sources_root = validate_sources_root(project_root, selected_sources_root)
        return materialize_workspace_image(
            project_root,
            manifest_path,
            resolved_sources_root,
            destination_root,
            selected_hooks,
        )

    _run(
        "materialize",
        operation,
        json_output=json_output,
        text=lambda result: rich_print(f"Workspace sources materialized at [bold]{result['destination_root']}[/bold]"),
    )
