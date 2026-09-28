"""Host-first workspace source management and generated development state."""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import shutil
import tempfile
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, NoReturn

from ruamel.yaml.error import YAMLError

from ..helpers.hashing import sha256_file
from ..models import GodooModules
from .editor import pyright_config_data, workspace_data
from .git import (
    CONTAINER_THIRDPARTY,
    encoded_path_component,
    inspect_repo_unlocked,
    repository_lock,
    repository_root,
    select_repo_unlocked,
    sync_repo_unlocked,
)
from .manifest import GodooManifest
from .specifications import GodooGitRepo
from .types import ResolvedSource, WorkspaceError, WorkspaceSettings

LOGGER = logging.getLogger(__name__)
CONTAINER_PROJECT_ROOT = Path("/odoo/godoo_workspace")


def _fail(message: str) -> NoReturn:
    """Raise a workspace error without obscuring the diagnostic."""
    raise WorkspaceError(message)


def _json_bytes(value: Any) -> bytes:
    """Serialize generated JSON to deterministic bytes for stable comparisons."""
    import json

    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _replace_bytes(path: Path, content: bytes) -> None:
    """Atomically publish one fully rendered generated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _project_generation_lock(settings: WorkspaceSettings, *, write: bool, create: bool):
    """Coordinate gOdoo publication without making read-only checks create state."""
    lock_path = settings.state_dir / "workspace.lock"
    if create:
        settings.state_dir.mkdir(parents=True, exist_ok=True)
    elif not lock_path.exists():
        yield
        return
    try:
        flags = os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
        fd = os.open(lock_path, flags, 0o600)
    except OSError as error:
        _fail(f"Unable to open workspace generation lock {lock_path}: {error}")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _manifest(settings: WorkspaceSettings) -> tuple[GodooManifest, str]:
    """Read the manifest and report invalid input as a workspace error."""
    try:
        digest = sha256_file(settings.manifest_path)
        manifest = GodooManifest.from_yaml_file(settings.manifest_path)
        if sha256_file(settings.manifest_path) != digest:
            _fail("Workspace manifest changed while reading; retry after saving it.")
    except (OSError, KeyError, ValueError, YAMLError) as error:
        _fail(f"Invalid workspace manifest {settings.manifest_path}: {error}")
    return manifest, digest


def _safe_extract_archive(archive: Path, destination: Path) -> None:
    """Extract a ZIP archive after rejecting paths outside the destination."""
    with zipfile.ZipFile(archive) as source:
        destination_resolved = destination.resolve()
        for member in source.infolist():
            target = (destination / member.filename).resolve()
            if destination_resolved != target and destination_resolved not in target.parents:
                _fail(f"Archive contains an unsafe path: {archive}: {member.filename}")
        source.extractall(destination)


def _directory_digest(root: Path) -> str:
    """Hash ordered NUL-delimited records of paths, types, links, and contents.

    Filesystem metadata is excluded from the digest.

    Returns:
        The hexadecimal SHA-256 digest of the records.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        if path.is_symlink():
            digest.update(b"link\0" + relative + b"\0" + os.fsencode(os.readlink(path)) + b"\0")
        elif path.is_dir():
            digest.update(b"directory\0" + relative + b"\0")
        elif path.is_file():
            digest.update(b"file\0" + relative + b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        else:
            _fail(f"Unsupported file in managed addon archive: {path}")
    return digest.hexdigest()


def _extract_archive_modules(archive: Path, temporary: Path) -> Path:
    """Extract one addon ZIP into the flattened managed-module layout."""
    extracted = temporary / "extracted"
    extracted.mkdir()
    try:
        _safe_extract_archive(archive, extracted)
    except (OSError, zipfile.BadZipFile) as error:
        _fail(f"Invalid addon archive {archive}: {error}")
    module_paths = sorted({manifest.parent for manifest in extracted.rglob("__manifest__.py")})
    if not module_paths:
        _fail(f"No Odoo modules found in archive: {archive}")
    modules = temporary / "modules"
    modules.mkdir()
    for module in module_paths:
        destination = modules / module.name
        if destination.exists():
            _fail(f"Duplicate module {module.name!r} in archive: {archive}")
        shutil.copytree(module, destination)
    return modules


@contextmanager
def _archive_cache_lock(settings: WorkspaceSettings, *, write: bool = True, create: bool = True):
    """Serialize publication into the shared archive cache."""
    archive_root = _validate_archive_cache_path(settings)
    if create:
        archive_root.parent.mkdir(parents=True, exist_ok=True)
        archive_root.mkdir(parents=True, exist_ok=True)
    lock_path = archive_root.parent / ".archives.lock"
    if not create and not lock_path.exists():
        yield
        return
    if lock_path.is_symlink():
        _fail(f"Managed archive cache paths must not be symlinks: {lock_path}")
    try:
        flags = os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
        fd = os.open(lock_path, flags, 0o600)
    except OSError as error:
        _fail(f"Unable to open managed archive cache lock {lock_path}: {error}")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _validate_archive_cache_path(settings: WorkspaceSettings) -> Path:
    """Reject symlinked archive-cache ancestry and paths outside the source root."""
    source_root = settings.sources_root
    archive_root = settings.thirdparty_source_root / ".archives"
    for path in (source_root, settings.thirdparty_source_root, archive_root):
        if path.is_symlink():
            _fail(f"Managed archive cache paths must not be symlinks: {path}")
    try:
        resolved_root = source_root.resolve()
        resolved_archive = archive_root.resolve()
        if not resolved_archive.is_relative_to(resolved_root):
            _fail(f"Managed archive cache is outside the source root: {archive_root}")
    except OSError as error:
        _fail(f"Unable to validate managed archive cache path {archive_root}: {error}")
    return archive_root


def _install_archive_cache(candidate: Path, target: Path, expected_digest: str) -> None:
    """Publish a new cache and reject changes to an existing shared directory."""
    if not target.is_symlink() and target.is_dir() and _directory_digest(target) == expected_digest:
        return
    if target.exists() or target.is_symlink():
        _fail(
            f"Managed addon archive cache was modified: {target}. Preserve it for inspection and move it aside before syncing."
        )
    try:
        candidate.rename(target)
    except OSError as error:
        if not target.is_symlink() and target.is_dir() and _directory_digest(target) == expected_digest:
            return
        _fail(f"Cannot publish addon archive cache at {target}: {error}")
    LOGGER.info("Installed generated addon archive cache at %s", target)


def _sync_archives(settings: WorkspaceSettings) -> list[dict[str, str]]:
    """Cache project addon archives and return their mount records."""
    _validate_archive_cache_path(settings)
    source_dir = settings.project_root / "thirdparty"
    if not source_dir.is_dir():
        return []
    records: list[dict[str, str]] = []
    with _archive_cache_lock(settings):
        archive_root = settings.thirdparty_source_root / ".archives"
        for archive in sorted(source_dir.glob("*.zip")):
            digest = sha256_file(archive)
            temporary = Path(tempfile.mkdtemp(prefix=f".{digest}.", dir=archive_root))
            try:
                modules = _extract_archive_modules(archive, temporary)
                content_digest = _directory_digest(modules)
                if sha256_file(archive) != digest:
                    _fail(f"Addon archive changed while reading: {archive}")
                target = archive_root / content_digest
                _install_archive_cache(modules, target, content_digest)
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
            records.append(
                {
                    "source_path": str(archive),
                    "sha256": digest,
                    "content_sha256": content_digest,
                    "host_path": str(target),
                    "container_path": str(CONTAINER_THIRDPARTY / f"archive_{encoded_path_component(archive.stem)}"),
                }
            )
    return records


def _source_selections(
    settings: WorkspaceSettings, manifest: GodooManifest
) -> list[tuple[str, str, GodooGitRepo, Path]]:
    """Pair manifest selections with their source roots and mount roles."""
    return [("odoo", "", manifest.odoo, settings.odoo_source_root)] + [
        ("addon", prefix, repo, settings.thirdparty_source_root) for prefix, repo in manifest.iter_thirdparty_repos()
    ]


def _repository_paths(settings: WorkspaceSettings, manifest: GodooManifest) -> list[Path]:
    """Return managed repository pools in a stable order."""
    return sorted(
        {
            repository_root(root, repo.url, role=role, prefix=prefix, name=repo.name)
            for role, prefix, repo, root in _source_selections(settings, manifest)
        },
        key=str,
    )


def _checked_archives(settings: WorkspaceSettings, *, verify_cache: bool = True) -> list[dict[str, str]]:
    """Resolve project archives and optionally verify cached extracted trees."""
    archive_root = _validate_archive_cache_path(settings)
    inventory = _archive_inventory(settings)
    if not inventory:
        return []
    records: list[dict[str, str]] = []
    for name, digest in inventory.items():
        archive = settings.project_root / "thirdparty" / name
        with tempfile.TemporaryDirectory(prefix=".archive-check-") as temporary:
            modules = _extract_archive_modules(archive, Path(temporary))
            content_digest = _directory_digest(modules)
        if sha256_file(archive) != digest:
            _fail(f"Addon archive changed while reading: {archive}")
        target = archive_root / content_digest
        if not target.is_dir() or target.is_symlink() or (verify_cache and _directory_digest(target) != content_digest):
            _fail(f"Managed addon archive cache changed or is missing: {target}")
        records.append(
            {
                "source_path": str(archive),
                "sha256": digest,
                "content_sha256": content_digest,
                "host_path": str(target),
                "container_path": str(CONTAINER_THIRDPARTY / f"archive_{encoded_path_component(archive.stem)}"),
            }
        )
    if _archive_inventory(settings) != inventory:
        _fail("Project addon archives changed while checking; retry after saving them.")
    return records


def _generated_paths(settings: WorkspaceSettings) -> dict[str, Path]:
    """Return generated editor artifacts still owned by gOdoo."""
    return {
        "vscode_workspace": settings.project_root / f"{settings.project_root.name}.code-workspace",
        "pyright_config": settings.project_root / "pyrightconfig.json",
    }


def _archive_inventory(settings: WorkspaceSettings) -> dict[str, str]:
    """Return current project archive names and content hashes."""
    source_dir = settings.project_root / "thirdparty"
    if not source_dir.is_dir():
        return {}
    return {archive.name: sha256_file(archive) for archive in sorted(source_dir.glob("*.zip"))}


def _retired_paths(settings: WorkspaceSettings) -> tuple[Path, ...]:
    """Return generated infrastructure artifacts now owned by downstream."""
    return (
        settings.state_dir / "docker-compose.sources.yml",
        settings.state_dir / "source-provenance.json",
        settings.state_dir / "docker-compose.debug.yml",
        settings.state_dir / "production-build.env",
        settings.state_dir / "production-sources.tar",
    )


def _validate_retired_paths(paths: tuple[Path, ...]) -> None:
    """Reject unsafe paths before removing obsolete generated artifacts."""
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            _fail(f"Retired generated workspace path is unsafe: {path}")


def _inspect_workspace(
    settings: WorkspaceSettings,
    manifest: GodooManifest,
) -> tuple[list[ResolvedSource], list[dict[str, str]]]:
    """Inspect manifest-selected worktrees and archives without changing them."""
    live = [
        inspect_repo_unlocked(
            repo,
            root=root,
            default_branch=manifest.default_branch,
            role=role,
            prefix=prefix,
        )
        for role, prefix, repo, root in _source_selections(settings, manifest)
    ]
    for source in live:
        path = source.host_path
        if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(settings.sources_root):
            _fail(f"Managed source path is outside shared source root or a symlink: {path}")
    return live, _checked_archives(settings)


def resolve_workspace_sources(
    settings: WorkspaceSettings,
) -> tuple[str, list[ResolvedSource], list[dict[str, str]]]:
    """Read manifest-selected sources without locks, writes, or generated state."""
    manifest, digest = _manifest(settings)
    sources, archives = _inspect_workspace(settings, manifest)
    if sha256_file(settings.manifest_path) != digest:
        _fail("Workspace manifest changed while resolving sources; retry after saving it.")
    return digest, sources, archives


def select_workspace_sources(
    settings: WorkspaceSettings,
) -> tuple[str, list[ResolvedSource], list[dict[str, str]]]:
    """Select existing worktrees from manifest and Git metadata without status scans."""
    manifest, digest = _manifest(settings)
    sources = [
        select_repo_unlocked(
            repo,
            root=root,
            default_branch=manifest.default_branch,
            role=role,
            prefix=prefix,
        )
        for role, prefix, repo, root in _source_selections(settings, manifest)
    ]
    archives = _checked_archives(settings, verify_cache=False)
    if sha256_file(settings.manifest_path) != digest:
        _fail("Workspace manifest changed while selecting sources; retry after saving it.")
    return digest, sources, archives


def runtime_environment(settings: WorkspaceSettings) -> dict[str, str]:
    """Return host-resolved runtime paths without publishing generated state.

    The caller owns passing these values to Compose.  This intentionally keeps
    Git/worktree inspection on the host and leaves no runtime handoff artifact.
    """
    _, sources, archives = select_workspace_sources(settings)
    odoo_sources = [source for source in sources if source.role == "odoo"]
    if len(odoo_sources) != 1:
        _fail("Manifest-selected sources must contain exactly one Odoo source.")

    odoo_root = odoo_sources[0].host_path
    addon_paths = [odoo_root / "addons", odoo_root / "odoo" / "addons"]
    project_addons = settings.project_root / "addons"
    if project_addons.is_dir() and next(GodooModules(project_addons).get_modules(), None):
        addon_paths.append(CONTAINER_PROJECT_ROOT / "addons")
    addon_paths.extend(source.host_path for source in sources if source.role == "addon")
    addon_paths.extend(Path(archive["host_path"]) for archive in archives)

    return {
        "GODOO_SOURCES_ROOT": str(settings.sources_root),
        "GODOO_RUNTIME_ODOO_PATH": str(odoo_sources[0].host_path),
        "GODOO_RUNTIME_ADDON_PATHS": os.pathsep.join(str(path) for path in dict.fromkeys(addon_paths)),
    }


def _workspace_state(
    settings: WorkspaceSettings,
    manifest_digest: str,
    sources: list[ResolvedSource],
    archives: list[dict[str, str]],
) -> dict[str, Any]:
    """Return the editor-oriented generated workspace state."""
    return {
        "project_root": str(settings.project_root),
        "manifest_digest": manifest_digest,
        "sources": [source.to_dict() for source in sources],
        "archives": archives,
        "vscode_workspace": str(_generated_paths(settings)["vscode_workspace"]),
        "pyright_config": str(_generated_paths(settings)["pyright_config"]),
    }


def _publish_workspace(
    settings: WorkspaceSettings,
    manifest_digest: str,
    sources: list[ResolvedSource],
    archives: list[dict[str, str]],
) -> dict[str, Any]:
    """Publish the editor workspace and remove obsolete generated artifacts."""
    paths = _generated_paths(settings)
    retired = _retired_paths(settings)
    _validate_retired_paths(retired)
    workspace = _json_bytes(workspace_data(settings, sources, archives))
    pyright_config = _json_bytes(pyright_config_data(sources, archives))
    snapshots = {path: path.read_bytes() if path.is_file() else None for path in (*paths.values(), *retired)}
    changed: list[Path] = []
    try:
        changed.append(paths["vscode_workspace"])
        _replace_bytes(paths["vscode_workspace"], workspace)
        changed.append(paths["pyright_config"])
        _replace_bytes(paths["pyright_config"], pyright_config)
        for path in retired:
            if path.exists():
                changed.append(path)
                path.unlink()
    except OSError as error:
        rollback_errors: list[str] = []
        for path in reversed(changed):
            try:
                previous = snapshots[path]
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    _replace_bytes(path, previous)
            except OSError as rollback_error:
                rollback_errors.append(f"{path}: {rollback_error}")
        details = f"Unable to publish generated workspace: {error}"
        if rollback_errors:
            details += f"; rollback errors: {'; '.join(rollback_errors)}"
        _fail(details)
    return _workspace_state(settings, manifest_digest, sources, archives)


def sync_workspace(settings: WorkspaceSettings) -> dict[str, Any]:
    """Synchronize managed source worktrees and regenerate the editor workspace."""
    with _project_generation_lock(settings, write=True, create=True), ExitStack() as stack:
        manifest, digest = _manifest(settings)
        for repository in _repository_paths(settings, manifest):
            stack.enter_context(repository_lock(repository, write=True))
        sources = [
            sync_repo_unlocked(repo, root=root, default_branch=manifest.default_branch, role=role, prefix=prefix)
            for role, prefix, repo, root in _source_selections(settings, manifest)
        ]
        archives = _sync_archives(settings)
        return _publish_workspace(settings, digest, sources, archives)


def configure_workspace(settings: WorkspaceSettings) -> dict[str, Any]:
    """Generate the VS Code workspace from already checked source worktrees."""
    with _project_generation_lock(settings, write=True, create=True):
        digest, sources, archives = resolve_workspace_sources(settings)
        return _publish_workspace(settings, digest, sources, archives)


def check_workspace(settings: WorkspaceSettings, *, sources_only: bool = False) -> dict[str, Any]:
    """Verify selected sources, then optionally check generated editor state."""
    if sources_only:
        digest, sources, archives = resolve_workspace_sources(settings)
        return _workspace_state(settings, digest, sources, archives)
    retired = _retired_paths(settings)
    if any(path.exists() or path.is_symlink() for path in retired):
        names = ", ".join(str(path) for path in retired if path.exists() or path.is_symlink())
        _fail(f"Retired generated workspace artifacts present: {names}; run workspace configure.")
    digest, sources, archives = resolve_workspace_sources(settings)
    expected = _json_bytes(workspace_data(settings, sources, archives))
    workspace = _generated_paths(settings)["vscode_workspace"]
    if not workspace.is_file() or workspace.read_bytes() != expected:
        _fail(f"Generated VS Code workspace is stale: {workspace}; run workspace configure.")
    expected_pyright = _json_bytes(pyright_config_data(sources, archives))
    pyright_config = _generated_paths(settings)["pyright_config"]
    if not pyright_config.is_file() or pyright_config.read_bytes() != expected_pyright:
        _fail(f"Generated Pyright config is stale: {pyright_config}; run workspace configure.")
    return _workspace_state(settings, digest, sources, archives)
