"""Check representative downstream Compose and production-image contracts."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from godoo_cli.models import GodooConfig
from godoo_cli.runtime.odoo import _extra_args_argv, build_launch_command

LOGGER = logging.getLogger(__name__)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _construct_compose_override(constructor: Any, node: Any) -> list[Any]:
    return constructor.construct_sequence(node, deep=True)


def _compose(name: str) -> dict[str, Any]:
    """Load one representative Compose document."""
    yaml = YAML(typ="safe")
    yaml.constructor.add_constructor("!override", _construct_compose_override)
    return yaml.load((REPOSITORY_ROOT / "docker" / name).read_text(encoding="utf-8"))


def test_example_app_owns_startup_and_init_is_maintenance() -> None:
    """Every app process prepares itself; the maintenance service is not a startup dependency."""
    compose = _compose("docker-compose.base.yml")
    services = compose["services"]
    initialization = services["init"]
    application = services["app"]
    assert initialization["command"] == ["godoo", "runtime", "init"]
    assert initialization["depends_on"]["db"] == {"condition": "service_healthy"}
    assert compose["services"]["db"]["healthcheck"]["test"] == [
        "CMD-SHELL",
        'test "$$(cat /proc/1/comm)" = postgres && pg_isready -h 127.0.0.1 -U "$${POSTGRES_USER}" -d postgres',
    ]
    postgresql_config = (REPOSITORY_ROOT / "config" / "postgresql.conf").read_text(encoding="utf-8")
    assert "listen_addresses = '*'" in postgresql_config
    assert application["command"] == [
        "godoo",
        "runtime",
        "start",
        "--extra-args=${ODOO_BIN_ARGS:+${ODOO_BIN_ARGS} }--proxy-mode",
    ]
    assert "GODOO_DEV_MODE" not in application["environment"]
    assert initialization["profiles"] == ["maintenance"]
    assert application["depends_on"] == {"db": {"condition": "service_healthy"}}
    assert application["environment"]["GODOO_UPDATE_CHANGED"] == "${GODOO_UPDATE_CHANGED:-true}"
    assert compose["x-runtime"]["environment"]["GODOO_X_SENDFILE"] == "${GODOO_X_SENDFILE:-true}"
    nginx = services["nginx"]
    assert nginx["depends_on"]["app"] == {"condition": "service_started"}
    assert nginx["ports"] == ["127.0.0.1:8069:80"]
    assert "ports" not in application
    assert "odoo_data:/var/lib/odoo:ro" in nginx["volumes"]
    assert "odoo_config:/odoo/config:ro" in nginx["volumes"]
    assert "../config/nginx.conf.template:/etc/nginx/templates/default.conf.template:ro" in nginx["volumes"]
    assert nginx["environment"] == {
        "GODOO_NGINX_WEBSOCKET_BACKEND": "${GODOO_NGINX_WEBSOCKET_BACKEND:-app:8072}",
        "NGINX_ENVSUBST_FILTER": "^GODOO_NGINX_WEBSOCKET_BACKEND$",
    }


@pytest.mark.parametrize("compose_file", ["docker-compose.base.yml", "docker-compose.dev.yml"])
@pytest.mark.parametrize("user_args", ["", "--workers=5 --limit-time-real=180"])
def test_nginx_launch_keeps_proxy_mode_with_zero_configured_workers(
    tmp_path: Path, compose_file: str, user_args: str
) -> None:
    """Nginx-facing launches enable proxy mode with zero configured workers."""
    launch = _compose(compose_file)["services"]["app"]["command"]
    prefix = f"{user_args} " if user_args else ""
    extra = [
        argument.split("=", 1)[1].replace("${ODOO_BIN_ARGS:+${ODOO_BIN_ARGS} }", prefix)
        for argument in launch
        if argument.startswith("--extra-args=")
    ]
    config = GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        db_name="proxy_test",
        db_user="odoo",
        db_password="",
        db_host="db",
        db_port=5432,
        db_filter="proxy_test",
        multithread_worker_count=0,
    )
    command = build_launch_command(config, _extra_args_argv(extra), upgrade_workspace_modules=False, save_config=False)
    assert "--proxy-mode" in command
    assert "--save" not in command
    assert "--update" not in command
    assert command[-1] == "--proxy-mode"
    if user_args:
        assert "--workers=5" in command
        assert "--limit-time-real=180" in command
    if compose_file == "docker-compose.dev.yml":
        assert command[-2:] == ["--workers=0", "--proxy-mode"]


def test_nginx_template_filter_selects_only_its_backend_input() -> None:
    """Template rendering leaves native Nginx variables untouched."""
    environment = _compose("docker-compose.base.yml")["services"]["nginx"]["environment"]
    pattern = re.compile(environment["NGINX_ENVSUBST_FILTER"])
    names = [
        "GODOO_NGINX_WEBSOCKET_BACKEND",
        "PREFIX_GODOO_NGINX_WEBSOCKET_BACKEND",
        "GODOO_NGINX_WEBSOCKET_BACKEND_SUFFIX",
        "http_upgrade",
        "scheme",
        "host",
        "odoo_upstream",
    ]
    assert [name for name in names if pattern.search(name)] == ["GODOO_NGINX_WEBSOCKET_BACKEND"]


def test_base_compose_supplies_selected_sources_to_builds() -> None:
    """Production builds receive the selected source context."""
    compose = _compose("docker-compose.base.yml")
    runtime = compose["x-runtime"]
    assert runtime["build"]["additional_contexts"] == {"godoo-sources": "${GODOO_SOURCES_ROOT:?Set GODOO_SOURCES_ROOT}"}
    assert "GODOO_SOURCES_ROOT" not in runtime["environment"]
    assert runtime["build"]["args"]["GODOO_PACKAGE"] == "${GODOO_PACKAGE:-godoo-cli}"
    assert runtime["build"]["target"] == "production"
    nginx_build = compose["services"]["nginx"]["build"]
    assert nginx_build["target"] == "nginx"
    assert nginx_build["additional_contexts"] == runtime["build"]["additional_contexts"]
    assert runtime["environment"]["GODOO_NGINX_CONFIG_PATH"] == "/odoo/config/nginx-static.conf"


def test_development_override_mounts_one_source_root() -> None:
    """Development adds source visibility without changing the production base."""
    compose = _compose("docker-compose.dev.yml")
    initialization = compose["services"]["init"]
    app = compose["services"]["app"]
    expected_volumes = [
        "..:/odoo/godoo_workspace",
        "${GODOO_SOURCES_ROOT}:${GODOO_SOURCES_ROOT}:ro",
    ]
    expected_environment = {
        "GODOO_SOURCES_ROOT": "${GODOO_SOURCES_ROOT}",
        "GODOO_RUNTIME_ODOO_PATH": "${GODOO_RUNTIME_ODOO_PATH}",
        "GODOO_RUNTIME_ADDON_PATHS": "${GODOO_RUNTIME_ADDON_PATHS}",
        "ODOO_MANIFEST": "/odoo/godoo_workspace/odoo_manifest.yml",
        "PYTHONPATH": "/odoo/godoo_workspace/src",
    }

    for service in (initialization, app):
        assert service["build"]["target"] == "development"
        assert service["build"]["additional_contexts"] == {"odoo-source": "${GODOO_RUNTIME_ODOO_PATH}"}
        assert service["volumes"] == expected_volumes
        assert {key: service["environment"][key] for key in expected_environment} == expected_environment

    assert "GODOO_DEV_MODE" not in initialization["environment"]
    assert app["command"] == [
        "python",
        "-m",
        "godoo_cli",
        "runtime",
        "start",
        "--extra-args=${ODOO_BIN_ARGS:+${ODOO_BIN_ARGS} }--workers=0 --proxy-mode",
    ]
    assert app["environment"]["GODOO_DEV_MODE"] == "1"
    assert compose["services"]["nginx"]["environment"] == {
        "GODOO_NGINX_WEBSOCKET_BACKEND": "${GODOO_NGINX_WEBSOCKET_BACKEND:-app:8069}"
    }
    assert compose["services"]["nginx"]["build"]["target"] == "nginx-development"
    assert compose["services"]["nginx"]["volumes"] == [
        "..:/odoo/godoo_workspace:ro",
        "${GODOO_SOURCES_ROOT}:${GODOO_SOURCES_ROOT}:ro",
    ]
    assert "websocket" not in compose["services"]
    assert "labels" not in app
    assert "labels" not in compose["services"]["nginx"]
    traefik = _compose("docker-compose.traefik.yml")["services"]
    assert set(traefik) == {"nginx"}


def test_development_target_uses_live_source_and_dependency_mounts() -> None:
    """Development stays independent of production materialization and source edits."""
    dockerfile = (REPOSITORY_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    dependencies = dockerfile.split("FROM tooling AS dependencies", maxsplit=1)[1].split(
        "FROM dependencies AS development", maxsplit=1
    )[0]
    development = dockerfile.split("FROM dependencies AS development", maxsplit=1)[1].split(
        "# Materialize selected sources", maxsplit=1
    )[0]
    assert "uv sync --frozen --no-dev --no-install-project" in dependencies
    assert "source=pyproject.toml,target=/build/project/pyproject.toml" in dependencies
    assert "source=uv.lock,target=/build/project/uv.lock" in dependencies
    assert "source=src" not in dependencies
    assert "godoo-cli" not in dependencies
    assert "from=odoo-source,source=requirements.txt" in development
    assert "from=godoo-sources" not in development
    assert "workspace materialize" not in development
    assert "GODOO_RUNTIME_MATERIALIZED" not in development
    assert "-r /tmp/odoo-requirements.txt" in development
    assert 'uv pip install --python "$VIRTUAL_ENV/bin/python" inotify' in development


def test_production_materialization_is_separate_from_dependency_install() -> None:
    """Only Odoo requirements connect materialization to dependency setup."""
    dockerfile = (REPOSITORY_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    materialized = dockerfile.split("FROM dependencies AS materialized", maxsplit=1)[1].split(
        "FROM dependencies AS production-dependencies", maxsplit=1
    )[0]
    production_dependencies = dockerfile.split("FROM dependencies AS production-dependencies", maxsplit=1)[1].split(
        "FROM production-dependencies AS production", maxsplit=1
    )[0]
    production = dockerfile.split("FROM production-dependencies AS production", maxsplit=1)[1]
    assert "from=godoo-sources,source=.,target=/build/sources,readonly" in materialized
    assert "source=.,target=/build/project,readonly" in materialized
    assert "workspace materialize" in materialized
    assert "--destination-root /image" in materialized
    assert "--mount=type=ssh" not in materialized
    assert "from=materialized,source=/image/odoo/odoo/requirements.txt" in production_dependencies
    assert "-r /tmp/odoo-requirements.txt" in production_dependencies
    assert "inotify" not in production_dependencies
    assert "inotify" not in production
    assert "COPY --from=materialized --chown=odoo:odoo /image/odoo/ /odoo/" in production
    assert "uv pip install --python" in production
    assert "ENV GODOO_RUNTIME_MATERIALIZED=1" in production
    assert "GODOO_RUNTIME_MATERIALIZED" not in materialized
    assert "GODOO_RUNTIME_MATERIALIZED" not in production_dependencies


def test_make_production_builds_compose_with_the_local_cli() -> None:
    """The production target uses Compose and installs the checked-out CLI."""
    makefile = (REPOSITORY_ROOT / "makefile").read_text(encoding="utf-8")
    assert (
        "$(BIN)/godoo workspace check --sources-only\n\tGODOO_PACKAGE=/build/project docker compose $(PROD_COMPOSE_FILES) up --build"
        in makefile
    )
    assert "scripts/production.py" not in makefile


def test_make_development_uses_the_single_proxy_stack() -> None:
    """The development target keeps websocket routing in the main proxy stack."""
    makefile = (REPOSITORY_ROOT / "makefile").read_text(encoding="utf-8")
    assert "GODOO_TRAEFIK_APP_WEBSOCKET_ENABLED" not in makefile
    assert "COMPOSE_PROFILES=dev" not in makefile


def test_traefik_override_routes_http_through_nginx() -> None:
    """Traefik sends all Odoo traffic to Nginx."""
    compose = _compose("docker-compose.traefik.yml")
    nginx = compose["services"]["nginx"]
    assert set(compose["services"]) == {"nginx"}
    assert nginx["networks"] == ["default", "traefik"]
    assert "traefik.http.services.${COMPOSE_PROJECT_NAME}.loadbalancer.server.port=80" in nginx["labels"]
