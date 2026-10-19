"""Plan and install Python dependencies declared by Odoo addon manifests."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from ...runtime.odoo import (
    dependency_addon_paths,
    install_dependency_requirements,
    odoo_source_get_version,
    source_dependency_requirements,
    workspace_module_names,
)

LOGGER = logging.getLogger(__name__)


def _runtime_addon_paths() -> list[Path] | None:
    """Read the path-separated runtime addon-path contract when it is present."""
    value = os.environ.get("GODOO_RUNTIME_ADDON_PATHS")
    if value is None:
        return None
    entries = value.split(os.pathsep)
    if not all(entries):
        message = "GODOO_RUNTIME_ADDON_PATHS must not contain empty path entries."
        raise typer.BadParameter(
            message,
            param_hint="GODOO_RUNTIME_ADDON_PATHS",
        )
    return [Path(entry) for entry in entries]


def runtime_dependencies(
    odoo_path: Annotated[
        Path | None,
        typer.Option("--odoo-path", envvar="GODOO_RUNTIME_ODOO_PATH", help="Selected Odoo source directory."),
    ] = None,
    addon_paths: Annotated[
        list[Path] | None,
        typer.Option("--addon-path", help="Addon source root; repeatable."),
    ] = None,
    workspace_addons: Annotated[
        Path | None,
        typer.Option(
            "--workspace-addons",
            envvar="GODOO_RUNTIME_WORKSPACE_ADDONS",
            help="Custom addon source root used as seeds when --module is absent.",
        ),
    ] = None,
    modules: Annotated[
        list[str] | None,
        typer.Option("--module", help="Explicit module seed; repeatable."),
    ] = None,
    requirements: Annotated[
        list[Path] | None,
        typer.Option("--requirements", help="Additional requirements file; repeatable."),
    ] = None,
    python: Annotated[
        Path | None,
        typer.Option("--python", help="Python interpreter target required by --install."),
    ] = None,
    install: Annotated[bool, typer.Option("--install", help="Install requirements with uv.")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Disable uv's cache for this install.")] = False,
) -> None:
    """Print source manifest requirements, or additively install them into --python."""
    resolved_odoo_path = odoo_path or Path(os.environ.get("ODOO_MAIN_FOLDER", "/odoo/odoo"))
    resolved_workspace = workspace_addons or Path(
        os.environ.get("ODOO_WORKSPACE_ADDON_LOCATION", "/odoo/godoo_workspace/addons")
    )
    thirdparty = Path(os.environ.get("ODOO_THIRDPARTY_ADDON_LOCATION", "/odoo/thirdparty"))
    if modules is None and not resolved_workspace.is_dir():
        message = f"Workspace addon path is not a directory: {resolved_workspace}"
        raise typer.BadParameter(message, param_hint="--workspace-addons")
    explicit_paths = addon_paths if addon_paths is not None else _runtime_addon_paths()
    resolved_paths = explicit_paths or dependency_addon_paths(resolved_odoo_path, resolved_workspace, thirdparty)
    placeholder = Path("/odoo/godoo_workspace/addons")
    resolved_paths = [
        *([resolved_workspace] if resolved_workspace.is_dir() else []),
        *(path for path in resolved_paths if path != placeholder),
    ]
    resolved_paths = list(dict.fromkeys(resolved_paths))
    target_major = odoo_source_get_version(resolved_odoo_path).major
    seed_modules = modules or workspace_module_names(resolved_workspace, target_major)
    manifest_requirements = source_dependency_requirements(resolved_paths, seed_modules)
    odoo_requirements = resolved_odoo_path / "requirements.txt"
    extra_files = requirements or []
    for requirement_file in extra_files:
        if not requirement_file.is_file():
            message = f"Requirements file is not a file: {requirement_file}"
            raise typer.BadParameter(message, param_hint="--requirements")
    LOGGER.debug(
        "Runtime dependency plan resolved: addon_paths=%d seeds=%d requirements=%d extras=%d install=%s",
        len(resolved_paths),
        len(seed_modules),
        len(manifest_requirements),
        len(extra_files),
        install,
    )

    if not install:
        if odoo_requirements.is_file():
            typer.echo(f"-r {odoo_requirements}")
        for requirement in manifest_requirements:
            typer.echo(requirement)
        for requirement_file in extra_files:
            typer.echo(f"-r {requirement_file}")
        return
    if python is None:
        message = "--python is required with --install."
        raise typer.BadParameter(message, param_hint="--python")
    try:
        install_dependency_requirements(
            python,
            manifest_requirements,
            odoo_requirements=odoo_requirements,
            extra_requirement_files=extra_files,
            no_cache=no_cache or os.environ.get("UV_NO_CACHE") not in {None, "", "0", "false", "False"},
        )
    except subprocess.CalledProcessError as error:
        message = "Runtime dependency installation failed; see uv diagnostics above."
        LOGGER.debug(message, exc_info=True)
        typer.echo(message, err=True)
        raise typer.Exit(code=error.returncode or 1) from error
