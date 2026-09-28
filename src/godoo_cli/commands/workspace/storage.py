"""Workspace CLI adapters."""

from pathlib import Path
from typing import Annotated, Any

import typer
from rich import print as rich_print

from ...workspace.storage import prune_sources, storage_report
from ..common import WorkspaceCLIArgs
from .common import _run, _sources_root


def workspace_storage(
    sources_root: Annotated[Path | None, WorkspaceCLIArgs.sources_root] = None,
    json_output: Annotated[bool, WorkspaceCLIArgs.json_output] = False,
) -> None:
    """List native Git worktree status for the shared source root."""

    def text(result: dict[str, Any]) -> None:
        rich_print(f"Git pools: {len(result['pools'])}; worktrees: {len(result['worktrees'])}.")
        typer.echo('Measure source disk usage with: du -sh "$GODOO_SOURCES_ROOT"')

    _run("storage report", lambda: storage_report(_sources_root(sources_root)), json_output=json_output, text=text)


def workspace_gc(
    sources_root: Annotated[Path | None, WorkspaceCLIArgs.sources_root] = None,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Apply native Git worktree pruning with Git's default expiry."),
    ] = False,
    json_output: Annotated[bool, WorkspaceCLIArgs.json_output] = False,
) -> None:
    """Preview or prune stale Git worktree records, preserving checkout files and branches."""

    def text(result: dict[str, Any]) -> None:
        for pool in result["pools"]:
            if pool["output"]:
                typer.echo(f"{pool['path']}:\n{pool['output']}")
        typer.echo("Git worktree pruning applied." if apply else "Preview only. Use --apply to prune stale records.")

    _run(
        "source cleanup",
        lambda: prune_sources(_sources_root(sources_root), apply=apply),
        json_output=json_output,
        text=text,
    )
