"""Check representative downstream Compose and production-image contracts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

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


def test_example_initializes_before_launching() -> None:
    """The application launches only after deployment initialization succeeds."""
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
    assert application["command"] == ["godoo", "runtime", "launch"]
    assert "GODOO_DEV_MODE" not in application["environment"]
    assert application["depends_on"]["init"] == {"condition": "service_completed_successfully"}
    assert compose["x-runtime"]["environment"]["GODOO_X_SENDFILE"] == "${GODOO_X_SENDFILE:-true}"
    nginx = services["nginx"]
    assert nginx["depends_on"]["app"] == {"condition": "service_started"}
    assert nginx["ports"] == ["127.0.0.1:8069:80"]
    assert "ports" not in application
    assert "odoo_data:/var/lib/odoo:ro" in nginx["volumes"]
    assert "../config/nginx.conf:/etc/nginx/conf.d/default.conf:ro" in nginx["volumes"]


def test_base_compose_supplies_selected_sources_to_builds() -> None:
    """Production builds receive the selected source context."""
    compose = _compose("docker-compose.base.yml")
    runtime = compose["x-runtime"]
    assert runtime["build"]["additional_contexts"] == {"godoo-sources": "${GODOO_SOURCES_ROOT:?Set GODOO_SOURCES_ROOT}"}
    assert "GODOO_SOURCES_ROOT" not in runtime["environment"]
    assert runtime["build"]["args"]["GODOO_PACKAGE"] == "${GODOO_PACKAGE:-godoo-cli}"


def test_development_override_mounts_one_source_root() -> None:
    """Development adds source visibility without changing the production base."""
    compose = _compose("docker-compose.dev.yml")
    initialization = compose["services"]["init"]
    app = compose["services"]["app"]
    websocket = compose["services"]["websocket"]
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

    for service in (initialization, app, websocket):
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
        "launch",
        "--extra-args=--workers=0",
    ]
    assert app["environment"]["GODOO_DEV_MODE"] == "1"
    assert websocket["command"] == [
        "sh",
        "-c",
        'exec "$$GODOO_RUNTIME_ODOO_PATH/odoo-bin" gevent --config "$$ODOO_CONF_PATH"',
    ]
    assert websocket["extends"] == {
        "file": "docker-compose.base.yml",
        "service": "init",
    }
    assert websocket["depends_on"]["init"] == {"condition": "service_completed_successfully"}
    assert websocket["networks"] == ["default", "traefik"]
    assert "GODOO_DEV_MODE" not in websocket["environment"]
    assert "labels" not in app
    assert "labels" not in websocket
    traefik = _compose("docker-compose.traefik.yml")["services"]
    assert traefik["app"]["labels"][0] == ("traefik.enable=${GODOO_TRAEFIK_APP_WEBSOCKET_ENABLED:-true}")
    assert traefik["websocket"]["profiles"] == ["dev"]
    assert traefik["websocket"]["labels"] == [
        "traefik.enable=true",
        "traefik.http.routers.${COMPOSE_PROJECT_NAME}-websocket.service=${COMPOSE_PROJECT_NAME}-websocket",
        "traefik.http.routers.${COMPOSE_PROJECT_NAME}-websocket.entrypoints=websecure",
        "traefik.http.routers.${COMPOSE_PROJECT_NAME}-websocket.rule=HostRegexp(`${TRAEFIK_HOST_REGEX}`) && PathPrefix(`/websocket`)",
        "traefik.http.services.${COMPOSE_PROJECT_NAME}-websocket.loadbalancer.server.port=8072",
    ]


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
        "$(BIN)/godoo workspace check --sources-only\n\tGODOO_PACKAGE=/build/project GODOO_TRAEFIK_APP_WEBSOCKET_ENABLED=true COMPOSE_PROFILES= docker compose $(PROD_COMPOSE_FILES) up --build"
        in makefile
    )
    assert "scripts/production.py" not in makefile


def test_make_development_enables_the_development_websocket_profile() -> None:
    """The development target routes WebSockets to its gevent service."""
    makefile = (REPOSITORY_ROOT / "makefile").read_text(encoding="utf-8")
    assert "GODOO_TRAEFIK_APP_WEBSOCKET_ENABLED=false COMPOSE_PROFILES=dev docker compose" in makefile


def test_traefik_override_routes_http_through_nginx() -> None:
    """Traefik routes HTTP through Nginx and websockets to Odoo."""
    compose = _compose("docker-compose.traefik.yml")
    nginx = compose["services"]["nginx"]
    app = compose["services"]["app"]
    assert "build" not in app
    assert nginx["networks"] == ["default", "traefik"]
    assert "traefik.http.services.${COMPOSE_PROJECT_NAME}.loadbalancer.server.port=80" in nginx["labels"]
    assert app["networks"] == ["default", "traefik"]
    assert "traefik.http.services.${COMPOSE_PROJECT_NAME}-websocket.loadbalancer.server.port=8072" in app["labels"]
    assert compose["services"]["websocket"]["profiles"] == ["dev"]
