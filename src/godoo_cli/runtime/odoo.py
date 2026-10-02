"""Odoo runtime behavior independent of CLI adapters."""

from __future__ import annotations

import configparser
import contextlib
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import psycopg2.errors
from packaging.specifiers import SpecifierSet

from ..database.state import DbBootstrapStatus, base_module_major, classify_bootstrap_state
from ..models import GodooConfig, GodooModules, OdooVersion

LOGGER = logging.getLogger(__name__)
OdooCommand = str | Sequence[str]
MODULE_STATES = ("installed", "to install", "to upgrade", "to remove")
SUPPORTED_ODOO_VERSION_SPECIFIER = ">=16"


class OdooVersionError(ValueError):
    """Version inspection failure with enough context for a CLI adapter."""

    def __init__(
        self,
        source_path: Path,
        expected_range: str | None,
        observed: str | None = None,
        parse_failure: str | None = None,
    ) -> None:
        """Capture source, expectation, and observed version details."""
        self.source_path, self.expected_range, self.observed, self.parse_failure = (
            source_path,
            expected_range,
            observed,
            parse_failure,
        )
        message = (
            f"Could not verify the Odoo runtime at {source_path}: {parse_failure}"
            if parse_failure
            else f"This command requires an Odoo version matching {expected_range!r}; found Odoo {observed} at {source_path}."
        )
        super().__init__(message)


@dataclass(frozen=True)
class OdooOptions:
    """Parsed Odoo command options relevant to gOdoo orchestration."""

    config_path: Path | None = None
    database: str | None = None
    db_user: str | None = None
    db_password: str | None = None
    db_host: str | None = None
    db_port: int | None = None
    db_sslmode: str | None = None
    data_dir: Path | None = None
    addon_paths: tuple[Path, ...] | None = None
    init_modules: tuple[str, ...] = ()
    update_modules: tuple[str, ...] = ()


_NAMES = {
    "-c": "config_path",
    "--config": "config_path",
    "-d": "database",
    "--database": "database",
    "--db-name": "database",
    "--db_name": "database",
    "-r": "db_user",
    "--db-user": "db_user",
    "--db_user": "db_user",
    "-w": "db_password",
    "-D": "data_dir",
    "--db-password": "db_password",
    "--db_password": "db_password",
    "--db-host": "db_host",
    "--db_host": "db_host",
    "--db-port": "db_port",
    "--db_port": "db_port",
    "--db-sslmode": "db_sslmode",
    "--db_sslmode": "db_sslmode",
    "--data-dir": "data_dir",
    "--data_dir": "data_dir",
    "--addons-path": "addon_paths",
    "--addons_path": "addon_paths",
    "-i": "init_modules",
    "--init": "init_modules",
    "-u": "update_modules",
    "--update": "update_modules",
}


def _parse_db_port(value: object) -> int | None:
    """Convert supported config sentinels or a port value to its effective form."""
    if value is None or value is False:
        return None
    if isinstance(value, str) and value.strip().lower() in {"none", "false"}:
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        return int(value)
    msg = f"Unsupported database port value: {value!r}"
    raise ValueError(msg)


def parse_odoo_options(arguments: Sequence[str]) -> OdooOptions:  # noqa: C901
    """Parse Odoo options; values following ``--`` are intentionally opaque."""
    # Normalize supported option spellings without interpreting Odoo's forwarded arguments.
    values: dict[str, Any] = {"init_modules": [], "update_modules": []}
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item == "--":
            break
        if len(item) > 2 and item[:2] in {"-w", "-D"}:
            option, sep, value = item[:2], "=", item[2:]
        else:
            option, sep, value = item.partition("=")
        field = _NAMES.get(option)
        if field is None:
            index += 1
            continue
        if not sep:
            if index + 1 == len(arguments):
                index += 1
                continue
            index += 1
            value = arguments[index]
        # Preserve repeated module options while later scalar options retain Odoo's last-value rule.
        if field in {"init_modules", "update_modules"}:
            values[field].extend(part.strip() for part in value.split(",") if part.strip())
        elif field == "addon_paths":
            values[field] = tuple(Path(part.strip()) for part in value.split(",") if part.strip())
        elif field in {"config_path", "data_dir"}:
            values[field] = Path(value)
        elif field == "db_port":
            values[field] = _parse_db_port(value)
        else:
            values[field] = value
        index += 1
    return OdooOptions(
        config_path=values.get("config_path"),
        database=values.get("database"),
        db_user=values.get("db_user"),
        db_password=values.get("db_password"),
        db_host=values.get("db_host"),
        db_port=values.get("db_port"),
        db_sslmode=values.get("db_sslmode"),
        data_dir=values.get("data_dir"),
        addon_paths=values.get("addon_paths"),
        init_modules=tuple(values["init_modules"]),
        update_modules=tuple(values["update_modules"]),
    )


def _config_values(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    return dict(parser.items("options")) if parser.has_section("options") else {}


def _config_file_overrides(configured: Mapping[str, str]) -> dict[str, Any]:
    """Translate Odoo config-file options into ``GodooConfig`` fields."""
    values: dict[str, Any] = {}
    for key, field in (
        ("db_name", "db_name"),
        ("db_user", "db_user"),
        ("db_password", "db_password"),
        ("db_host", "db_host"),
        ("db_port", "db_port"),
        ("db_sslmode", "db_sslmode"),
        ("data_dir", "data_dir"),
    ):
        value = configured.get(key, configured.get("database") if field == "db_name" else None)
        if isinstance(value, str) and value.strip().lower() in {"none", "false"}:
            continue
        if value is not None:
            values[field] = int(value) if field == "db_port" else Path(value) if field == "data_dir" else value
    addons = tuple(Path(value.strip()) for value in configured.get("addons_path", "").split(",") if value.strip())
    if addons:
        values["resolved_addon_paths"] = addons
    return values


def _option_overrides(options: OdooOptions) -> dict[str, Any]:
    """Translate parsed command-line options into ``GodooConfig`` fields."""
    values = {
        "db_name" if field == "database" else field: value
        for field in ("database", "db_user", "db_password", "db_host", "db_port", "db_sslmode", "data_dir")
        if (value := getattr(options, field)) is not None
    }
    if options.addon_paths:
        values["resolved_addon_paths"] = options.addon_paths
    return values


def resolve_odoo_config(
    config: GodooConfig,
    arguments: Sequence[str] = (),
    *,
    direct: Mapping[str, object | None] | None = None,
) -> GodooConfig:
    """Resolve config-file defaults, argv, then supplied CLI values (including empty/zero)."""
    options = parse_odoo_options(arguments)
    config_path = options.config_path or config.odoo_conf_path
    values: dict[str, object] = {}
    if options.config_path is not None:
        values["odoo_conf_path"] = options.config_path
    # Apply the normal Odoo precedence order: config file, command line, then CLI adapter.
    configured = _config_values(config_path)
    values.update(_config_file_overrides(configured))
    values.update(_option_overrides(options))
    if direct:
        values.update(
            {
                key: value
                for key, value in direct.items()
                if value is not None and not (key == "db_port" and value is False)
            }
        )
    return replace(config, **values) if values else config


def odoo_database_args(
    *,
    db_name: str | None,
    db_user: str | None,
    db_password: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_sslmode: str | None = None,
) -> list[str]:
    """Build Odoo database option pairs without discarding explicit empty values."""
    result: list[str] = []
    for option, value in (
        ("--database", db_name),
        ("--db_user", db_user),
        ("--db_password", db_password),
        ("--db_host", db_host),
        ("--db_port", db_port),
        ("--db_sslmode", db_sslmode),
    ):
        if value is not None:
            result.extend((option, str(value)))
    return result


def execution_python(project_root: Path | None = None) -> Path:
    """Return the Python executable selected for an Odoo child process."""
    environment = (
        Path(os.environ["VIRTUAL_ENV"]) if os.environ.get("VIRTUAL_ENV") else (project_root or Path.cwd()) / ".venv"
    )
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.is_file():
        message = f"Odoo execution environment is missing its interpreter: {executable}"
        raise FileNotFoundError(message)
    return executable


def odoo_debugger_attached() -> bool:
    """Return whether the parent process already has a debugpy client."""
    debugpy = sys.modules.get("debugpy")
    return debugpy is not None and debugpy.is_client_connected()


def sanitize_odoo_password(
    argv: Sequence[str], environment: Mapping[str, str] | None = None
) -> tuple[list[str], dict[str, str]]:
    """Move Odoo password options before ``--`` into ``PGPASSWORD``."""
    result: list[str] = []
    password: str | None = None
    index = 0
    terminated = False
    while index < len(argv):
        value = argv[index]
        if value == "--":
            terminated = True
            result.append(value)
        elif not terminated and value in {"-w", "--db_password", "--db-password"}:
            if index + 1 < len(argv):
                index += 1
                password = argv[index]
                result.append("--db_password=")
            else:
                result.append(value)
        elif not terminated and value.startswith("-w") and not value.startswith("-w="):
            password = value[2:]
            result.append("--db_password=")
        elif not terminated and any(value.startswith(f"{name}=") for name in ("-w", "--db_password", "--db-password")):
            password = value.split("=", 1)[1]
            result.append("--db_password=")
        else:
            result.append(value)
        index += 1
    env = dict(os.environ if environment is None else environment)
    if password is not None:
        env["PGPASSWORD"] = password
    return result, env


def odoo_command_argv(command: OdooCommand) -> list[str]:
    """Normalize a legacy command string or argv sequence."""
    return shlex.split(command) if isinstance(command, str) else list(command)


def run_odoo_command(command: OdooCommand, **kwargs: Any) -> subprocess.CompletedProcess:  # noqa: C901
    """Run Odoo without a shell, forwarding stop signals to its process group."""
    if "shell" in kwargs:
        message = "Odoo commands must not be run through a shell"
        raise ValueError(message)
    project_root = kwargs.pop("project_root", None)
    argv, kwargs["env"] = sanitize_odoo_password(odoo_command_argv(command), kwargs.get("env"))
    if argv and Path(argv[0]).name == "odoo-bin":
        argv = [str(execution_python(project_root)), *argv]
    LOGGER.debug("Running Odoo command: %s", argv)
    if threading.current_thread() is not threading.main_thread():
        return subprocess.run(argv, **kwargs)
    check = kwargs.pop("check", False)
    timeout = kwargs.pop("timeout", None)
    process_input = kwargs.pop("input", None)
    if process_input is not None:
        if kwargs.get("stdin") is not None:
            message = "stdin and input cannot be used together"
            raise ValueError(message)
        kwargs["stdin"] = subprocess.PIPE
    if kwargs.pop("capture_output", False):
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            message = "stdout and stderr cannot be used with capture_output"
            raise ValueError(message)
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # A dedicated process group lets shutdown reach Odoo and any children it creates.
    kwargs.setdefault("start_new_session", True)
    child: subprocess.Popen | None = None

    def forward(signum: int, _frame: Any) -> None:
        if child is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signum) if kwargs["start_new_session"] else child.send_signal(signum)

    # Install forwarding only while this invocation owns the child, then restore the caller's handlers.
    previous = {signum: signal.signal(signum, forward) for signum in (signal.SIGTERM, signal.SIGINT)}
    try:
        with subprocess.Popen(argv, **kwargs) as child:
            try:
                stdout, stderr = child.communicate(input=process_input, timeout=timeout)
            except BaseException:
                forward(signal.SIGKILL, None)
                child.communicate()
                raise
            returncode = child.returncode if child.returncode >= 0 else 128 - child.returncode
            result = subprocess.CompletedProcess(argv, returncode, stdout, stderr)
            if check:
                result.check_returncode()
            return result
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def odoo_bin_get_version(path: Path) -> OdooVersion:
    """Inspect and parse the Odoo version reported by ``odoo-bin``."""
    source = path / "odoo-bin"
    output = run_odoo_command([str(source.absolute()), "--version"], capture_output=True, text=True).stdout
    match = re.match(r"(?P<text>.*) (?P<major>\d+)\.(?P<minor>\d+)", output)
    if not match:
        raise OdooVersionError(source, None, output.strip(), "unparseable version output")
    return OdooVersion(text=match.group("text"), major=int(match.group("major")), minor=int(match.group("minor")))


def require_odoo_version(path: Path, version_specifier: str) -> OdooVersion:
    """Return a compatible Odoo version or raise a domain version error."""
    try:
        version = odoo_bin_get_version(path)
    except OdooVersionError:
        raise
    except Exception as error:
        raise OdooVersionError(path / "odoo-bin", version_specifier, parse_failure=str(error)) from error
    if version.semantic not in SpecifierSet(version_specifier):
        raise OdooVersionError(path / "odoo-bin", version_specifier, version.raw)
    return version


def require_supported_odoo_major(major: int, path: Path) -> int:
    """Reject Odoo majors outside the runtime support contract."""
    version = OdooVersion(text=f"{major}.0", major=major, minor=0)
    if version.semantic not in SpecifierSet(SUPPORTED_ODOO_VERSION_SPECIFIER):
        raise OdooVersionError(
            path / "odoo-bin",
            SUPPORTED_ODOO_VERSION_SPECIFIER,
            f"{major}.0",
        )
    return major


def require_supported_odoo_runtime(path: Path) -> OdooVersion:
    """Return the Odoo runtime version when its major is supported."""
    return require_odoo_version(path, SUPPORTED_ODOO_VERSION_SPECIFIER)


def _runtime_database_names(config: GodooConfig) -> list[str]:
    """Return configured database names in their effective order."""
    return list(dict.fromkeys(name.strip() for name in (config.db_name or "").split(",") if name.strip()))


def _database_load_guidance(db_name: str, runtime_major: int, detail: str) -> str:
    """Explain how to replace or select an incompatible initialized database."""
    return (
        f"Database '{db_name}' {detail} This runtime requires Odoo {runtime_major}. "
        f"Load a matching archive with `godoo db load <archive>`, or select the Odoo "
        f"service result for Odoo {runtime_major}."
    )


def require_runtime_database_major(config: GodooConfig) -> int | None:
    """Require each initialized configured database to match this Odoo runtime."""
    db_names = _runtime_database_names(config)
    if not db_names:
        return None

    runtime_major = require_supported_odoo_runtime(config.odoo_install_folder).major
    for db_name in db_names:
        connection = config.db_connection.with_db(db_name)
        state = classify_bootstrap_state(connection)
        if state in {DbBootstrapStatus.NO_DB, DbBootstrapStatus.EMPTY_DB}:
            continue
        if state is not DbBootstrapStatus.BOOTSTRAPPED:
            detail = "is initialized but has no usable installed Odoo base module."
            raise RuntimeError(_database_load_guidance(db_name, runtime_major, detail))
        try:
            database_major = base_module_major(connection)
        except RuntimeError as error:
            detail = f"has no usable Odoo base module version ({error})."
            raise RuntimeError(_database_load_guidance(db_name, runtime_major, detail)) from error
        if database_major != runtime_major:
            detail = f"contains Odoo {database_major}."
            raise RuntimeError(_database_load_guidance(db_name, runtime_major, detail))
    return runtime_major


def _database_modules(config: GodooConfig) -> dict[str, bool]:
    """Read installed modules and identify database-only modules."""
    started_at = time.monotonic()
    if not config.db_name:
        return {}
    modules: dict[str, list[bool]] = {}
    for name in filter(None, (value.strip() for value in config.db_name.split(","))):
        connection = (
            config.db_connection.with_db(name)
            if name != config.db_name and hasattr(config.db_connection, "with_db")
            else config.db_connection
        )
        try:
            with connection.connect() as cursor:
                cursor.execute("SELECT name FROM ir_module_module WHERE state = ANY(%s)", (list(MODULE_STATES),))
                database_modules = [row[0] for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'ir_module_module' "
                    "AND column_name = 'imported')"
                )
                imported_modules: set[str] = set()
                imported_column = cursor.fetchone()
                if imported_column is not None and imported_column[0]:
                    cursor.execute("SELECT name FROM ir_module_module WHERE imported")
                    imported_modules = {row[0] for row in cursor.fetchall()}
                for module in database_modules:
                    modules.setdefault(module, []).append(module in imported_modules)
        except psycopg2.OperationalError as target_error:
            try:
                with connection.with_db("postgres", readonly=True).connect() as cursor:
                    cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)", [name])
                    row = cursor.fetchone()
            except Exception:
                raise target_error from None
            if not row or row[0] is not False:
                raise target_error from None
        except (psycopg2.errors.InvalidCatalogName, psycopg2.errors.UndefinedTable):
            continue
    result = {module: module == "studio_customization" or all(imported) for module, imported in modules.items()}
    LOGGER.debug(
        "Odoo dependency preflight database module discovery completed: databases=%d modules=%d elapsed=%.3fs",
        len([value for value in config.db_name.split(",") if value.strip()]),
        len(result),
        time.monotonic() - started_at,
    )
    return result


def _selected_modules(
    config: GodooConfig,
    options: OdooOptions,
    *,
    ignore_missing_installed_modules: bool = False,
) -> list[str]:
    """Resolve requested and installed modules from one effective configuration."""
    database_modules = _database_modules(config)
    requested = [*database_modules, *options.init_modules, *options.update_modules]
    if "all" in requested:
        requested = [item for item in requested if item != "all"]
    elif "base" not in requested:
        requested.insert(0, "base")
    registry_started_at = time.monotonic()
    registry = GodooModules(list(options.addon_paths or config.addon_paths))
    LOGGER.debug(
        "Odoo dependency preflight module registry constructed: phase=selection addon_paths=%d elapsed=%.3fs",
        len(registry.addon_paths),
        time.monotonic() - registry_started_at,
    )
    resolution_started_at = time.monotonic()
    modules = []
    for name in dict.fromkeys(requested):
        try:
            module = registry.get_module(name)
        except ModuleNotFoundError:
            if name == "studio_customization" or database_modules.get(name, False):
                continue
            if ignore_missing_installed_modules and name in database_modules and name != "base":
                LOGGER.info(
                    "Skipping unavailable installed module during pre-upgrade dependency preflight: %s",
                    name,
                )
                continue
            raise
        else:
            modules.append(module)
    LOGGER.debug(
        "Odoo dependency preflight selected module resolution completed: "
        "requested=%d resolved=%d addon_paths=%d elapsed=%.3fs",
        len(dict.fromkeys(requested)),
        len(modules),
        len(registry.addon_paths),
        time.monotonic() - resolution_started_at,
    )
    closure_started_at = time.monotonic()
    resolved = list(
        dict.fromkeys(
            [
                *(module.name for module in modules),
                *(module.name for module in registry.get_module_dependencies(modules, strict=True)),
            ]
        )
    )
    LOGGER.debug(
        "Odoo dependency preflight module dependency closure completed: "
        "selected=%d closure=%d addon_paths=%d elapsed=%.3fs",
        len(modules),
        len(resolved),
        len(registry.addon_paths),
        time.monotonic() - closure_started_at,
    )
    return resolved


def selected_modules(config: GodooConfig, arguments: Sequence[str] = ()) -> list[str]:
    """Resolve requested and installed modules including dependencies."""
    return _selected_modules(resolve_odoo_config(config, arguments), parse_odoo_options(arguments))


def _dependency_requirements(
    config: GodooConfig,
    options: OdooOptions,
    *,
    ignore_missing_installed_modules: bool = False,
) -> list[str]:
    """Return external Python requirements from one effective configuration."""
    registry_started_at = time.monotonic()
    registry = GodooModules(list(options.addon_paths or config.addon_paths))
    LOGGER.debug(
        "Odoo dependency preflight module registry constructed: phase=manifest addon_paths=%d elapsed=%.3fs",
        len(registry.addon_paths),
        time.monotonic() - registry_started_at,
    )
    discovery_started_at = time.monotonic()
    selected = _selected_modules(
        config,
        options,
        ignore_missing_installed_modules=ignore_missing_installed_modules,
    )
    requirements: list[str] = []
    manifest_count = 0
    for module in registry.get_modules(selected):
        manifest_count += 1
        requirements.extend(
            "python-ldap" if requirement == "ldap" else requirement
            for requirement in module.manifest.get("external_dependencies", {}).get("python", [])
        )
    result = list(dict.fromkeys(requirements))
    LOGGER.debug(
        "Odoo dependency preflight external dependency manifest discovery completed: "
        "selected=%d manifests=%d requirements=%d addon_paths=%d elapsed=%.3fs",
        len(selected),
        manifest_count,
        len(result),
        len(registry.addon_paths),
        time.monotonic() - discovery_started_at,
    )
    return result


def dependency_requirements(
    config: GodooConfig,
    arguments: Sequence[str] = (),
    *,
    resolved: bool = False,
    ignore_missing_installed_modules: bool = False,
) -> list[str]:
    """Return ordered external Python requirements for selected modules."""
    config_started_at = time.monotonic()
    effective = config if resolved else resolve_odoo_config(config, arguments)
    LOGGER.debug(
        "Odoo dependency preflight configuration resolved: addon_paths=%d elapsed=%.3fs",
        len(effective.addon_paths),
        time.monotonic() - config_started_at,
    )
    options_started_at = time.monotonic()
    options = parse_odoo_options(arguments)
    LOGGER.debug(
        "Odoo dependency preflight command options parsed: init=%d update=%d addon_path_override=%s elapsed=%.3fs",
        len(options.init_modules),
        len(options.update_modules),
        options.addon_paths is not None,
        time.monotonic() - options_started_at,
    )
    return _dependency_requirements(
        effective,
        options,
        ignore_missing_installed_modules=ignore_missing_installed_modules,
    )


def preflight_for_config(
    config: GodooConfig,
    extra_args: Sequence[str] = (),
    *,
    project_root: Path | None = None,
    include_module_dependencies: bool = True,
    ignore_missing_installed_modules: bool = False,
) -> None:
    """Install required Python dependencies for an effective Odoo configuration."""
    preflight_started_at = time.monotonic()
    arguments = list(extra_args)
    if any(item in {"--help", "-h", "--version"} for item in arguments):
        return
    effective = config
    if include_module_dependencies:
        require_runtime_database_major(effective)
    environment = (project_root or Path.cwd()).resolve() / ".venv"
    if not os.environ.get("VIRTUAL_ENV") and not environment.exists():
        environment.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["uv", "venv", str(environment)], check=True)
    dependency_discovery_started_at = time.monotonic()
    requirements = (
        dependency_requirements(
            effective,
            arguments,
            resolved=True,
            ignore_missing_installed_modules=ignore_missing_installed_modules,
        )
        if include_module_dependencies
        else []
    )
    LOGGER.debug(
        "Odoo dependency preflight dependency discovery completed: enabled=%s requirements=%d elapsed=%.3fs",
        include_module_dependencies,
        len(requirements),
        time.monotonic() - dependency_discovery_started_at,
    )
    source_requirements = effective.odoo_install_folder / "requirements.txt"
    install_source_requirements = source_requirements.is_file() and (
        os.environ.get("GODOO_ODOO_REQUIREMENTS_PREINSTALLED") != "1"
    )
    if not requirements and not install_source_requirements:
        LOGGER.debug(
            "Odoo dependency preflight completed without installation: elapsed=%.3fs",
            time.monotonic() - preflight_started_at,
        )
        return
    command = ["uv", "pip", "install", "--python", str(execution_python(project_root))]
    if install_source_requirements:
        command.extend(["-r", str(source_requirements)])
    try:
        install_started_at = time.monotonic()
        subprocess.run([*command, *requirements], check=True)
        LOGGER.debug(
            "Odoo dependency preflight dependency installation completed: "
            "requirements=%d source_requirements=%s elapsed=%.3fs total_elapsed=%.3fs",
            len(requirements),
            install_source_requirements,
            time.monotonic() - install_started_at,
            time.monotonic() - preflight_started_at,
        )
    except subprocess.CalledProcessError as error:
        message = "Odoo dependency preflight failed; a required system package may be missing."
        raise RuntimeError(message) from error


def write_odoo_config(path: Path, options: Mapping[str, str]) -> None:
    """Merge Odoo option values into a UTF-8 configuration file."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    if not parser.has_section("options"):
        parser.add_section("options")
    parser["options"].update(options)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        parser.write(stream)


def _extra_args_argv(extra_cmd_args: Sequence[str]) -> list[str]:
    """Normalize legacy option chunks while preserving separated argv values."""
    result: list[str] = []
    for chunk in extra_cmd_args:
        result.extend(shlex.split(chunk) if chunk.startswith("-") else [chunk])
    return result


def _odoo_config_args(config: GodooConfig, *, save: bool) -> list[str]:
    if save:
        config.odoo_conf_path.parent.mkdir(parents=True, exist_ok=True)
    return [
        "--config",
        str(config.odoo_conf_path.absolute()),
        "--data-dir",
        str(config.data_dir.absolute()),
        *(["--save"] if save else []),
        *odoo_database_args(
            db_name=config.db_name,
            db_user=config.db_user,
            db_password=config.db_password,
            db_host=config.db_host,
            db_port=config.db_port,
            db_sslmode=config.db_sslmode,
        ),
        f"--db-filter=^{config.db_filter}$",
    ]


def build_launch_command(
    config: GodooConfig,
    extra_cmd_args: Sequence[str],
    upgrade_workspace_modules: bool = True,
    odoo_version: OdooVersion | None = None,
    save_config: bool | None = None,
) -> list[str]:
    """Build the Odoo server argv from one effective runtime configuration."""
    extra = _extra_args_argv(extra_cmd_args)
    update: list[str] = []
    if upgrade_workspace_modules and not any(
        item in {"-u", "--update"} or item.startswith("--update=") for item in extra
    ):
        modules = [
            module.name
            for module in GodooModules(config.workspace_addon_path).get_modules()
            if odoo_version is None or module.version.split(".")[0] == str(odoo_version.major)
        ]
        if modules:
            update = ["--update", ",".join(modules)]
    workers = config.multithread_worker_count
    if workers == -1:
        workers = (os.cpu_count() or 2) // 2
    worker_args = ["--workers", str(workers)]
    if workers > 0:
        worker_args.append("--proxy-mode")
    return [
        str(config.odoo_bin_path.absolute()),
        *update,
        *_odoo_config_args(config, save=not config.odoo_conf_path.exists() if save_config is None else save_config),
        *worker_args,
        *extra,
    ]


def build_bootstrap_command(
    config: GodooConfig,
    addon_paths: Sequence[Path],
    extra_cmd_args: Sequence[str] = (),
    install_workspace_modules: bool = True,
    odoo_version: OdooVersion | None = None,
) -> list[str]:
    """Build Odoo's native initialization argv from one effective configuration."""
    from ..database.state import DbBootstrapStatus, classify_bootstrap_state

    extra = _extra_args_argv(extra_cmd_args)
    forbidden = {"--save", "-s", "--test-enable", "--test-file", "--test-tags", "-t"}
    if any(
        arg in forbidden or arg.startswith("-t") or any(arg.startswith(f"{option}=") for option in forbidden)
        for arg in extra
    ):
        message = "Initialization cannot save its no-HTTP setting or enable HTTP-based tests."
        raise ValueError(message)
    action: list[str] = []
    if install_workspace_modules and not any(
        item in {"-i", "--init", "-u", "--update"} or item.startswith(("--init=", "--update=")) for item in extra
    ):
        modules = [
            module.name
            for module in GodooModules([config.workspace_addon_path]).get_modules()
            if odoo_version is None or module.version.split(".")[0] == str(odoo_version.major)
        ] or ["base", "web"]
        action = [
            "--update"
            if classify_bootstrap_state(config.db_connection) is DbBootstrapStatus.BOOTSTRAPPED
            else "--init",
            ",".join(modules),
        ]
    command = [
        str(config.odoo_bin_path.absolute()),
        *action,
        *_odoo_config_args(config, save=False),
        "--load-language",
        config.languages,
        "--stop-after-init",
        "--addons-path",
        ",".join(str(path.absolute()) for path in addon_paths if path and path.exists()),
        *extra,
    ]
    workers = config.multithread_worker_count
    if workers == -1:
        workers = (os.cpu_count() or 2) // 2
    if workers > 0:
        command.extend(["--proxy-mode", "--workers", str(workers)])
    return [*command, "--no-http"]


def prepare_runtime(config: GodooConfig, *, x_sendfile: bool | None = None) -> None:
    """Write Odoo configuration without database initialization or reconciliation."""
    require_supported_odoo_runtime(config.odoo_install_folder)
    options = {
        "data_dir": str(config.data_dir.absolute()),
        "addons_path": ",".join(str(path.absolute()) for path in config.addon_paths),
        "db_name": config.db_name,
        "db_user": config.db_user,
        "db_password": config.db_password,
        "db_host": config.db_host,
        "db_port": str(config.db_port),
        "dbfilter": f"^{config.db_filter}$" if config.db_filter else "",
        "http_interface": "0.0.0.0",
        "http_enable": "True",
    }
    if config.db_sslmode is not None:
        options["db_sslmode"] = config.db_sslmode
    if x_sendfile is not None:
        options["x_sendfile"] = str(x_sendfile)
    write_odoo_config(config.odoo_conf_path, options)


def x_sendfile_enabled(config: GodooConfig, requested: bool | None) -> bool:
    """Read the effective X-Sendfile setting before runtime init changes it."""
    if requested is not None:
        return requested
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config.odoo_conf_path, encoding="utf-8")
    return parser.getboolean("options", "x_sendfile", fallback=False)


def bootstrap_runtime(
    config: GodooConfig,
    *,
    extra_cmd_args: Sequence[str] = (),
    install_workspace_modules: bool = True,
    install_base_modules: bool = True,
) -> int:
    """Initialize an Odoo database after additive dependency preflight."""
    odoo_version = require_supported_odoo_runtime(config.odoo_install_folder)
    command = build_bootstrap_command(
        config,
        config.addon_paths,
        extra_cmd_args,
        install_workspace_modules,
        odoo_version,
    )
    if not install_base_modules and "--init" in command and command[command.index("--init") + 1] == "base,web":
        del command[command.index("--init") : command.index("--init") + 2]
    preflight_for_config(config, command[1:])
    return run_odoo_command(command).returncode


def build_odoo_shell_command(config: GodooConfig) -> list[str]:
    """Build an Odoo shell argv from a resolved configuration."""
    require_supported_odoo_runtime(config.odoo_install_folder)
    command = [
        str(config.odoo_bin_path.absolute()),
        "shell",
        "--no-http",
        "--data-dir",
        str(config.data_dir.absolute()),
    ]
    if config.odoo_conf_path.exists():
        command.extend(["--config", str(config.odoo_conf_path.absolute())])
    else:
        command.extend(["--addons-path", ",".join(str(path.absolute()) for path in config.addon_paths)])
    command.extend(
        odoo_database_args(
            db_name=config.db_name,
            db_user=config.db_user,
            db_password=config.db_password,
            db_host=config.db_host,
            db_port=config.db_port,
            db_sslmode=config.db_sslmode,
        )
    )
    preflight_for_config(config, command[1:])
    return command


def set_report_url(config: GodooConfig, report_url: str) -> None:
    """Set the URL Odoo uses to fetch assets while rendering PDF reports."""
    script = f"env['ir.config_parameter'].sudo().set_param('report.url', {report_url!r})\nenv.cr.commit()\n"
    result = run_odoo_command(build_odoo_shell_command(config), input=script, text=True)
    if result.returncode:
        message = f"Could not set Odoo report.url (exit code {result.returncode})."
        raise RuntimeError(message)


def bootstrap_and_prep_launch_cmd(
    config: GodooConfig,
    odoo_demo: bool,
    dev_mode: bool,
    extra_launch_args: Sequence[str] = (),
    extra_bootstrap_args: Sequence[str] = (),
    log_file_path: Path | None = None,
    install_workspace_addons: bool = True,
    launch_or_bootstrap: bool = False,
) -> int | list[str]:
    """Bootstrap when needed, then return the prepared launch argv."""
    from ..database.state import DbBootstrapStatus, classify_bootstrap_state

    launch_args = list(extra_launch_args)
    if log_file_path is not None:
        log_file_path.unlink(missing_ok=True)
        launch_args.extend(["--logfile", str(log_file_path.absolute())])
    if classify_bootstrap_state(config.db_connection) is not DbBootstrapStatus.BOOTSTRAPPED:
        bootstrap_args = list(extra_bootstrap_args)
        if not odoo_demo:
            bootstrap_args.append("--without-demo")
        result = bootstrap_runtime(
            config, extra_cmd_args=bootstrap_args, install_workspace_modules=install_workspace_addons
        )
        if result or launch_or_bootstrap:
            return result
        install_workspace_addons = False
    if dev_mode:
        launch_args.extend(["--dev", "xml,qweb,reload"])
    if config.multithread_worker_count == 0:
        launch_args.extend(["--workers", "0"])
    command = build_launch_command(
        config,
        launch_args,
        upgrade_workspace_modules=install_workspace_addons,
    )
    preflight_for_config(config, command[1:])
    return command
