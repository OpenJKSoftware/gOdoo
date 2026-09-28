"""Workspace editor-layout contracts."""

import json
from pathlib import Path

from godoo_cli.workspace.editor import pyright_config_data, workspace_data
from godoo_cli.workspace.types import ResolvedSource, WorkspaceSettings


def test_workspace_uses_portable_project_paths_and_absolute_sources(tmp_path: Path) -> None:
    """Container-generated workspaces remain valid on the host."""
    project = tmp_path / "Odoo 19.0"
    source_path = tmp_path / "sources" / "odoo" / "worktrees" / "19.0"
    settings = WorkspaceSettings(project, project / "odoo_manifest.yml", tmp_path / "sources")
    source = ResolvedSource(
        role="odoo",
        prefix="",
        name="odoo",
        url="https://github.com/odoo/odoo.git",
        branch="19.0",
        worktree_branch="godoo/19.0",
        requested_commit="",
        base_commit="base",
        resolved_commit="resolved",
        merge_from=(),
        merge_commits=(),
        recipe_fingerprint="recipe",
        host_path=source_path,
        container_path=Path("/odoo/odoo"),
    )

    workspace = workspace_data(settings, [source], [])

    assert workspace["folders"][0] == {"name": "gOdoo", "path": "."}
    assert workspace["folders"][1]["path"] == str(source_path)
    assert workspace["settings"]["python.defaultInterpreterPath"] == "${workspaceFolder:gOdoo}/.venv/bin/python"
    assert workspace["settings"]["python.analysis.extraPaths"] == [
        "${workspaceFolder:gOdoo}/addons",
        str(source_path),
    ]
    assert "${workspaceFolder}" not in json.dumps(workspace)
    pyright = pyright_config_data([source], [])
    assert pyright == {
        "venvPath": ".",
        "venv": ".venv",
        "pythonVersion": "3.11",
        "include": ["src", "addons", "scripts"],
        "extraPaths": ["addons", str(source.host_path)],
    }
    assert {"localRoot": "${workspaceFolder:gOdoo}", "remoteRoot": "/odoo/godoo_workspace"} in workspace["launch"][
        "configurations"
    ][0]["pathMappings"]
    assert {"localRoot": str(source_path), "remoteRoot": str(source_path)} in workspace["launch"]["configurations"][0][
        "pathMappings"
    ]
