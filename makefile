.DEFAULT_GOAL := test

BIN := .venv/bin
PYTEST_ARGS ?=
DEV_UP_ARGS ?=
BASE_COMPOSE_FILES := -f docker/docker-compose.base.yml
DEV_COMPOSE_FILES := $(BASE_COMPOSE_FILES) -f docker/docker-compose.traefik.yml -f docker/docker-compose.dev.yml
PROD_COMPOSE_FILES := $(BASE_COMPOSE_FILES) -f docker/docker-compose.traefik.yml

.PHONY: setup check-env lint typecheck test pre-commit sync workspace configure dev prod stop reset test-odoo-integration

setup: # Install native development tools without replacing existing environment.
	uv sync --locked

check-env: # Require repository environment setup before operational commands.
	@test -f .env || (echo "Missing .env; copy .env.sample to .env and configure it first." && exit 1)

lint: # Run repository quality checks on host.
	$(BIN)/ruff check .
	$(BIN)/ruff check . --preview --select DOC201,DOC202,DOC402,DOC403,DOC501
	$(BIN)/ruff format --check .
	$(BIN)/python scripts/check_docstring_style.py
	$(BIN)/pylint .

typecheck: # Run Pyright against the generated project configuration.
	@test -f pyrightconfig.json || (echo "Missing pyrightconfig.json; run make workspace first." && exit 1)
	$(BIN)/pyright --project pyrightconfig.json

test: check-env # Run unit tests in current development environment.
	$(BIN)/pytest $(PYTEST_ARGS)

pre-commit: # Run all pre-commit checks on host.
	$(BIN)/pre-commit run --all-files

sync: check-env # Synchronize only verified managed Git worktrees.
	$(BIN)/godoo workspace sync

workspace: check-env # Regenerate the VS Code workspace on the host.
	$(BIN)/godoo workspace configure

configure: workspace # Compatibility alias for workspace generation.

dev: check-env # Build and start the bind-mounted development stack.
	@set -eu; \
		eval "$$($(BIN)/godoo workspace runtime-env)"; \
		export GODOO_SOURCES_ROOT GODOO_RUNTIME_ODOO_PATH GODOO_RUNTIME_ADDON_PATHS; \
		GODOO_TRAEFIK_APP_WEBSOCKET_ENABLED=false COMPOSE_PROFILES=dev docker compose $(DEV_COMPOSE_FILES) up --build $(DEV_UP_ARGS)

prod: check-env # Check selected sources, then build and start the production stack.
	$(BIN)/godoo workspace check --sources-only
	GODOO_PACKAGE=/build/project GODOO_TRAEFIK_APP_WEBSOCKET_ENABLED=true COMPOSE_PROFILES= docker compose $(PROD_COMPOSE_FILES) up --build

stop: check-env # Stop every running container in this Compose project.
	@set -eu; \
		containers="$$(docker compose $(BASE_COMPOSE_FILES) ps --quiet --orphans)"; \
		if [ -n "$$containers" ]; then docker stop $$containers; fi

reset: check-env # Remove the active stack and all database, filestore, and configuration volumes.
	docker compose $(BASE_COMPOSE_FILES) down --volumes --remove-orphans


test-odoo-integration: check-env # Run isolated real-runtime tests in prepared execution environment.
	GODOO_RUN_ODOO_INTEGRATION=1 $(BIN)/pytest -m odoo_integration --no-cov tests/integration/test_odoo_integration.py
