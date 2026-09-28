"""Expose workspace CLI command groups."""

import typer

from .configure import workspace_configure, workspace_runtime_env
from .image import workspace_materialize
from .storage import workspace_gc, workspace_storage
from .sync import workspace_check, workspace_sync


def workspace_cli_app() -> typer.Typer:
    """Create workspace source-management command group."""
    app = typer.Typer(
        no_args_is_help=True,
        help="Manage shared host source worktrees and generated project state.",
    )
    app.command("sync")(workspace_sync)
    app.command("check")(workspace_check)
    app.command("configure")(workspace_configure)
    app.command("runtime-env")(workspace_runtime_env)
    app.command("materialize")(workspace_materialize)
    app.command("storage")(workspace_storage)
    app.command("gc")(workspace_gc)
    return app
