"""Resolve CLI configuration and enforce command policy."""

import logging
import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from typer import BadParameter, Exit, echo

from ..models import GodooConfig, GodooModules
from ..runtime.odoo import OdooVersionError, require_odoo_version, resolve_odoo_config
from ..workspace.types import ResolvedSource

LOGGER = logging.getLogger(__name__)


def resolve_command_config(
    *,
    odoo_main_path: Path,
    odoo_conf_path: Path,
    workspace_addon_path: Path,
    data_dir: Path | None = None,
    db_name: str | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_filter: str | None = None,
    arguments: list[str] | tuple[str, ...] = (),
    extra: Mapping[str, object | None] | None = None,
) -> GodooConfig:
    """Resolve one command's config-file, argv, and explicit CLI inputs."""
    direct: dict[str, object | None] = {
        "db_name": db_name,
        "db_user": db_user,
        "db_password": db_password,
        "db_host": db_host,
        "db_port": db_port,
        "db_filter": db_filter,
    }
    if data_dir is not None:
        direct["data_dir"] = data_dir
    if extra:
        direct.update(extra)
    config = resolve_odoo_config(
        GodooConfig(
            odoo_install_folder=odoo_main_path,
            odoo_conf_path=odoo_conf_path,
            workspace_addon_path=workspace_addon_path,
            thirdparty_addon_path=odoo_main_path.parent / "thirdparty",
            data_dir=data_dir or Path("/var/lib/odoo"),
        ),
        arguments,
        direct=direct,
    )
    return _resolve_development_source_config(config)


def _development_sources():
    """Resolve manifest-selected sources when the shared root is mounted."""
    sources_root = os.environ.get("GODOO_SOURCES_ROOT")
    if not sources_root:
        return None

    manifest_path = os.environ.get("ODOO_MANIFEST", "odoo_manifest.yml")

    # Lazy imports keep the common CLI option module independent from workspace setup.
    from ..workspace import resolve_workspace_sources
    from ..workspace.types import WorkspaceError, WorkspaceSettings

    try:
        settings = WorkspaceSettings.create(
            project_root=Path.cwd(),
            manifest_path=Path(manifest_path),
            sources_root=Path(sources_root),
        )
        _, sources, archives = resolve_workspace_sources(settings)
    except WorkspaceError as error:
        message = f"Could not resolve development sources: {error}"
        raise BadParameter(
            message,
            param_hint="GODOO_SOURCES_ROOT",
        ) from error

    return settings, sources, archives


def _selected_development_odoo_source(sources: list[ResolvedSource]) -> ResolvedSource:
    """Return the Odoo source selected by the development manifest."""
    odoo_sources = [source for source in sources if source.role == "odoo"]
    if len(odoo_sources) != 1:
        message = "The manifest-selected development sources must contain exactly one Odoo source."
        raise BadParameter(
            message,
            param_hint="GODOO_SOURCES_ROOT",
        )

    return odoo_sources[0]


_RUNTIME_ODOO_PATH_ENV = "GODOO_RUNTIME_ODOO_PATH"
_RUNTIME_ADDON_PATHS_ENV = "GODOO_RUNTIME_ADDON_PATHS"


def _runtime_source_paths() -> tuple[Path, tuple[Path, ...]] | None:
    """Return host-resolved runtime paths without inspecting the workspace."""
    odoo_value = os.environ.get(_RUNTIME_ODOO_PATH_ENV)
    addons_value = os.environ.get(_RUNTIME_ADDON_PATHS_ENV)
    if odoo_value is None and addons_value is None:
        return None
    if not odoo_value or not addons_value:
        message = f"{_RUNTIME_ODOO_PATH_ENV} and {_RUNTIME_ADDON_PATHS_ENV} must be provided together."
        raise BadParameter(message, param_hint=_RUNTIME_ODOO_PATH_ENV)

    addon_values = addons_value.split(os.pathsep)
    if not all(addon_values):
        message = f"{_RUNTIME_ADDON_PATHS_ENV} must not contain empty path entries."
        raise BadParameter(message, param_hint=_RUNTIME_ADDON_PATHS_ENV)

    sources_root = _runtime_sources_root()
    odoo_path = _validate_runtime_source_path(
        Path(odoo_value),
        _RUNTIME_ODOO_PATH_ENV,
        allowed_roots=(sources_root,) if sources_root is not None else (),
    )
    addon_paths = tuple(
        _validate_runtime_source_path(
            Path(value),
            _RUNTIME_ADDON_PATHS_ENV,
            allowed_roots=(sources_root, Path.cwd().resolve()) if sources_root is not None else (),
        )
        for value in addon_values
    )
    return odoo_path, addon_paths


def _runtime_sources_root() -> Path | None:
    """Return the mounted source root when runtime paths need confinement."""
    sources_root = os.environ.get("GODOO_SOURCES_ROOT")
    if not sources_root:
        return None
    root = Path(sources_root)
    if not root.is_absolute():
        message = "GODOO_SOURCES_ROOT must be absolute when runtime paths are set."
        raise BadParameter(message, param_hint="GODOO_SOURCES_ROOT")
    if not root.is_dir():
        message = "GODOO_SOURCES_ROOT must be an existing directory when runtime paths are set."
        raise BadParameter(message, param_hint="GODOO_SOURCES_ROOT")
    return root.resolve(strict=True)


def _validate_runtime_source_path(
    path: Path,
    environment_name: str,
    *,
    allowed_roots: tuple[Path, ...],
) -> Path:
    """Validate one host path that Compose has mounted into this container."""
    if not path.is_absolute():
        message = f"{environment_name} paths must be absolute: {path}"
        raise BadParameter(message, param_hint=environment_name)
    if not path.is_dir():
        message = f"{environment_name} path must be an existing directory: {path}"
        raise BadParameter(message, param_hint=environment_name)

    resolved_path = path.resolve(strict=True)
    if allowed_roots:
        try:
            next(root for root in allowed_roots if resolved_path.is_relative_to(root))
        except StopIteration as error:
            message = f"{environment_name} path escapes GODOO_SOURCES_ROOT and the project root: {path}"
            raise BadParameter(message, param_hint=environment_name) from error
    return resolved_path


def resolve_development_odoo_main_path(odoo_main_path: Path) -> Path:
    """Return the selected development Odoo worktree when available."""
    resolved = _development_sources()
    if resolved is None:
        return odoo_main_path
    _settings, sources, _archives = resolved
    return _selected_development_odoo_source(sources).host_path


def _resolve_development_source_config(config: GodooConfig) -> GodooConfig:
    """Use manifest-selected source paths when the shared root is mounted."""
    runtime_paths = _runtime_source_paths()
    if runtime_paths is not None:
        odoo_root, addon_paths = runtime_paths
        return replace(
            config,
            odoo_install_folder=odoo_root,
            resolved_addon_paths=addon_paths,
        )
    if os.getenv("GODOO_RUNTIME_MATERIALIZED") == "1":
        return replace(config, resolved_addon_paths=None)

    resolved = _development_sources()
    if resolved is None:
        return config
    settings, sources, archives = resolved
    odoo_root = _selected_development_odoo_source(sources).host_path
    addon_paths = [odoo_root / "addons", odoo_root / "odoo" / "addons"]
    if config.workspace_addon_path.is_dir() and GodooModules(config.workspace_addon_path).get_modules():
        addon_paths.append(config.workspace_addon_path)
    addon_paths.extend(source.host_path for source in sources if source.role == "addon")
    addon_paths.extend(Path(archive["host_path"]) for archive in archives)

    return replace(
        config,
        odoo_install_folder=odoo_root,
        manifest_path=settings.manifest_path,
        resolved_addon_paths=tuple(dict.fromkeys(addon_paths)),
    )


def require_cli_odoo_version(path: Path, version_specifier: str):
    """Translate a domain version failure at the command boundary."""
    try:
        return require_odoo_version(path, version_specifier)
    except OdooVersionError as error:
        raise BadParameter(str(error), param_hint="--odoo-main-path") from error


def check_dangerous_command() -> None:
    """Refuse state-changing commands outside an explicit development environment."""
    if os.environ.get("WORKSPACE_IS_DEV", "").lower() != "true":
        message = "This is a dangerous command. Only allowed in Dev Mode."
        LOGGER.error(message)
        echo(message, err=True)
        raise Exit(code=1)
