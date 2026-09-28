"""Render the generated VS Code workspace for selected Odoo sources."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .types import ResolvedSource

if TYPE_CHECKING:
    from .types import WorkspaceSettings


CONTAINER_PROJECT = Path("/odoo/godoo_workspace")
PROJECT_FOLDER_NAME = "gOdoo"
PROJECT_WORKSPACE_FOLDER = f"${{workspaceFolder:{PROJECT_FOLDER_NAME}}}"


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
            "python.defaultInterpreterPath": f"{PROJECT_WORKSPACE_FOLDER}/.venv/bin/python",
            "python.languageServer": "Pylance",
            "python.analysis.extraPaths": python_extra_paths,
            "python.testing.pytestEnabled": False,
            "python.testing.unittestEnabled": False,
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
        "pythonVersion": "3.11",
        "include": ["src", "addons", "scripts"],
        "extraPaths": [
            "addons",
            *(str(source.host_path) for source in sources),
            *(archive["host_path"] for archive in archives),
        ],
    }
