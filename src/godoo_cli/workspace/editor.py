"""Render the generated VS Code workspace for selected Odoo sources."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonc

from .types import ResolvedSource, WorkspaceError

if TYPE_CHECKING:
    from .types import WorkspaceSettings


CONTAINER_PROJECT = Path("/odoo/godoo_workspace")
PROJECT_FOLDER_NAME = "gOdoo"
PROJECT_WORKSPACE_FOLDER = f"${{workspaceFolder:{PROJECT_FOLDER_NAME}}}"


def _project_settings(project_root: Path) -> dict[str, Any]:
    """Read project editor settings in VS Code's JSON-with-comments format."""
    path = project_root / ".vscode" / "settings.json"
    try:
        content = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as error:
        message = f"Unable to read VS Code settings {path}: {error}"
        raise WorkspaceError(message) from error
    try:
        data = jsonc.loads(content)
    except jsonc.JSONDecodeError as error:
        message = f"Invalid VS Code settings {path}: {error}"
        raise WorkspaceError(message) from error
    if not isinstance(data, dict):
        message = f"VS Code settings must be an object: {path}"
        raise WorkspaceError(message)
    return data


def workspace_data(
    settings: WorkspaceSettings,
    sources: list[ResolvedSource],
    archives: list[dict[str, str]],
) -> dict[str, Any]:
    """Build the host-valid multi-root VS Code workspace definition."""
    folders: list[dict[str, str]] = [{"name": PROJECT_FOLDER_NAME, "path": "."}]
    folders.extend(
        {"name": f"Odoo {source.branch}", "path": str(source.host_path)} for source in sources if source.role == "odoo"
    )
    folders.extend(
        {
            "name": f"{source.prefix}_{source.name}/{source.branch}",
            "path": str(source.host_path),
        }
        for source in sources
        if source.role == "addon"
    )
    folders.extend(
        {"name": f"ZIP {Path(archive['source_path']).stem}", "path": archive["host_path"]} for archive in archives
    )
    python_extra_paths = [
        f"{PROJECT_WORKSPACE_FOLDER}/addons",
        *(str(source.host_path) for source in sources),
        *(archive["host_path"] for archive in archives),
    ]
    path_mappings = [
        {"localRoot": PROJECT_WORKSPACE_FOLDER, "remoteRoot": str(CONTAINER_PROJECT)},
        *({"localRoot": str(source.host_path), "remoteRoot": str(source.host_path)} for source in sources),
        *({"localRoot": archive["host_path"], "remoteRoot": archive["host_path"]} for archive in archives),
    ]
    return {
        "folders": folders,
        "settings": {
            **_project_settings(settings.project_root),
            "python.analysis.extraPaths": python_extra_paths,
        },
        "launch": {
            "version": "0.2.0",
            "configurations": [
                {
                    "name": "gOdoo: attach",
                    "type": "debugpy",
                    "request": "attach",
                    "connect": {"host": "localhost", "port": settings.debug_port},
                    "pathMappings": path_mappings,
                },
            ],
        },
    }


def pyright_config_data(
    sources: list[ResolvedSource],
    archives: list[dict[str, str]],
) -> dict[str, Any]:
    """Build the project-root Pyright configuration for selected sources."""
    return {
        "venvPath": ".",
        "venv": ".venv",
        "include": ["src", "addons", "scripts"],
        "extraPaths": [
            "addons",
            *(str(source.host_path) for source in sources),
            *(archive["host_path"] for archive in archives),
        ],
    }
