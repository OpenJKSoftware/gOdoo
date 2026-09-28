"""Copy manifest-selected sources into a production image filesystem."""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from ..__about__ import __version__
from .types import ResolvedSource, WorkspaceError, WorkspaceSettings

LOGGER = logging.getLogger(__name__)

PROVENANCE_SCHEMA_VERSION = 2
CONTAINER_PROJECT = PurePosixPath("/odoo/godoo_workspace")
IMAGE_PROVENANCE_PATH = PurePosixPath("/odoo/godoo-source-provenance.json")
PROJECT_ADDONS = Path("addons")


def _container_target(value: PurePosixPath, *, label: str) -> PurePosixPath:
    """Validate a canonical absolute destination beneath /odoo."""
    if not value.is_absolute() or len(value.parts) < 3 or value.parts[1] != "odoo":
        message = f"Image source target must stay below /odoo {label}: {value}"
        raise WorkspaceError(message)
    if ".." in value.parts or "\\" in str(value):
        message = f"Image source target is not canonical {label}: {value}"
        raise WorkspaceError(message)
    return value


def _image_target(value: PurePosixPath, destination_root: Path, *, label: str) -> Path:
    """Map a canonical /odoo destination below the requested image root."""
    value = _container_target(value, label=label)
    root = destination_root.resolve()
    target = root.joinpath(*value.parts[1:])
    if not target.resolve().is_relative_to(root):
        message = f"Image source target escapes its destination root {label}: {value}"
        raise WorkspaceError(message)
    return target


def _context_source(root: Path, candidate: Path, *, label: str) -> tuple[Path, Path]:
    """Resolve an existing directory confined to its mounted host context."""
    context = root.resolve()
    source = candidate.resolve()
    if not context.is_dir() or root.is_symlink():
        message = f"Image source context is missing or unsafe: {root}"
        raise WorkspaceError(message)
    if not source.is_relative_to(context) or not source.is_dir() or candidate.is_symlink():
        message = f"Image source is missing or escapes its context {label}: {candidate}"
        raise WorkspaceError(message)
    return source, source.relative_to(context)


def _project_source(project_root: Path, relative_path: Path, *, label: str) -> tuple[Path, Path]:
    """Resolve one selected project directory beneath the project root."""
    if (
        relative_path.is_absolute()
        or relative_path == Path(".")
        or ".." in relative_path.parts
        or "\\" in str(relative_path)
    ):
        message = f"Project path must be a relative directory below project root: {relative_path}"
        raise WorkspaceError(message)
    return _context_source(project_root, project_root / relative_path, label=label)


def _source_record(source: ResolvedSource, relative_path: Path) -> dict[str, Any]:
    """Return schema 2 provenance for one worktree without its host path."""
    record = source.to_dict()
    record.pop("host_path", None)
    record["source_path"] = relative_path.as_posix()
    return record


def _archive_record(archive: dict[str, str], relative_path: Path) -> dict[str, str]:
    """Return schema 2 provenance for one archive tree without host paths."""
    return {key: value for key, value in archive.items() if key not in {"host_path", "source_path"}} | {
        "source_path": relative_path.as_posix()
    }


def _copy_source(source: Path, target: Path, *, label: str) -> None:
    """Copy one selected tree, leaving Git metadata out of the image."""
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        message = f"Image source target is not a directory: {label}: {target}"
        raise WorkspaceError(message)
    target.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Copying production source %s to %s", source, target)
    shutil.copytree(
        source,
        target,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git"),
        dirs_exist_ok=True,
    )


def materialize_workspace_image(
    project_root: Path,
    manifest_path: Path,
    sources_root: Path,
    destination_root: Path,
    hook_dirs: Sequence[Path] = (),
) -> dict[str, Any]:
    """Copy only manifest-selected trees to canonical image paths."""
    from .operations import select_workspace_sources

    settings = WorkspaceSettings.create(
        project_root=project_root,
        manifest_path=manifest_path,
        sources_root=sources_root,
    )
    manifest_digest, sources, archives = select_workspace_sources(settings)

    selections: list[tuple[str, Path, PurePosixPath, dict[str, Any]]] = []
    source_records: list[dict[str, Any]] = []
    archive_records: list[dict[str, Any]] = []
    for source in sources:
        label = source.name
        host_path, relative_path = _context_source(settings.sources_root, source.host_path, label=label)
        selections.append(
            (
                label,
                host_path,
                _container_target(PurePosixPath(source.container_path), label=label),
                _source_record(source, relative_path),
            )
        )
        source_records.append(_source_record(source, relative_path))
    for archive in archives:
        label = Path(archive["source_path"]).name
        host_path, relative_path = _context_source(settings.sources_root, Path(archive["host_path"]), label=label)
        selections.append(
            (
                label,
                host_path,
                _container_target(PurePosixPath(archive["container_path"]), label=label),
                _archive_record(archive, relative_path),
            )
        )
        archive_records.append(_archive_record(archive, relative_path))

    project_paths = list(dict.fromkeys((PROJECT_ADDONS, *hook_dirs)))
    selected_project_paths: list[str] = []
    for relative_path in project_paths:
        label = relative_path.as_posix()
        host_path, normalized_path = _project_source(settings.project_root, relative_path, label=label)
        target = _container_target(CONTAINER_PROJECT.joinpath(*normalized_path.parts), label=label)
        selections.append((label, host_path, target, {}))
        selected_project_paths.append(normalized_path.as_posix())

    targets: list[PurePosixPath] = []
    copies: list[dict[str, str]] = []
    for name, source, target, _record in selections:
        if any(
            target == existing or target.is_relative_to(existing) or existing.is_relative_to(target)
            for existing in targets
        ):
            message = f"Image source targets overlap at {name}: {target}"
            raise WorkspaceError(message)
        targets.append(target)
        copies.append(
            {
                "name": name,
                "source": str(source),
                "target": str(_image_target(target, destination_root, label=name)),
            }
        )

    provenance_target = _container_target(IMAGE_PROVENANCE_PATH, label="provenance")
    if any(
        provenance_target == target
        or provenance_target.is_relative_to(target)
        or target.is_relative_to(provenance_target)
        for target in targets
    ):
        message = f"Image source selection overlaps provenance destination: {provenance_target}"
        raise WorkspaceError(message)

    provenance = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "godoo_version": __version__,
        "manifest_sha256": manifest_digest,
        "project": {
            "name": settings.project_root.name,
            "source_paths": selected_project_paths,
            "container_path": str(CONTAINER_PROJECT),
        },
        "sources": source_records,
        "archives": archive_records,
    }

    for name, source, target, _record in selections:
        _copy_source(source, _image_target(target, destination_root, label=name), label=name)
    provenance_path = _image_target(provenance_target, destination_root, label="provenance")
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    LOGGER.info("Wrote production source provenance to %s", provenance_path)
    return {
        "copied": copies,
        "destination_root": str(destination_root.resolve()),
        "provenance_path": str(provenance_path),
    }
