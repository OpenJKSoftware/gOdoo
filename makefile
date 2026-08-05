.DEFAULT_GOAL := dev

.PHONY: dev prepare bootstrap launch quick offline bare kill reset-container reset-container-hard reset reset-hard rebuild restore-dump-to-template lint

dev: # Prepare, bootstrap a missing database, then launch with development mode.
	ODOO_BIN_BOOTSTRAP_ARGS='--odoo-demo' scripts/launchodoo.sh --dev-mode

prepare: # Synchronize source and prepare configuration and Python dependencies.
	scripts/launchodoo.sh --prepare-only

bootstrap: # Prepare and initialize a missing database; does not start Odoo.
	ODOO_BIN_BOOTSTRAP_ARGS='--odoo-demo' scripts/launchodoo.sh --bootstrap-only

launch: # Start an existing prepared Odoo runtime; does not bootstrap or upgrade it.
	godoo launch --dev-mode

quick: launch # Alias for starting an existing development runtime.

offline: # Prepare/bootstrap/launch without fetching source repositories.
	ODOO_BIN_BOOTSTRAP_ARGS='--odoo-demo' scripts/launchodoo.sh --dev-mode --skip-source-sync

bare: # Prepare/bootstrap/launch without installing workspace modules.
	ODOO_BIN_BOOTSTRAP_ARGS='--no-install-workspace-modules' scripts/launchodoo.sh

kill: # Search for odoo-bin processes and kill them.
	pgrep -f odoo-bin | xargs kill -s KILL

reset-container: # Deletes DevContainer volumes and restarts the environment.
	scripts/reset_devcontainer.sh

reset-container-hard: # Also deletes VSCode extension volumes and forces a rebuild.
	scripts/reset_devcontainer.sh --hard

# Compatibility aliases. Prefer the explicit container target names above.
reset: reset-container

reset-hard: reset-container-hard

rebuild:
	cd docker && docker compose -f docker-compose.base.yml -f docker-compose.devcontainer.yml build

restore-dump-to-template: # Load an Odoo-native ZIP archive into the template DB.
	godoo db load --force --db-name odoo_template remote_instance_data.zip

lint:
	hatch run dev:lint
