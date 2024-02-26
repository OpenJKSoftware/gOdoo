.DEFAULT_GOAL := launch

launch: # To be called from inside the Devcontainer
# Bootstrap or Launch, Install/Upgrade Workspace addons, Keep running.
	GODOO_BOOSTRAP_ARGS="--odoo-demo" GODOO_LAUNCH_ARGS="--dev-mode" scripts/launchodoo.sh

quick: # To be called from inside the Devcontainer
# Bootstrap or Launch, Install/Upgrade Workspace addons, Keep running.
	godoo launch --dev-mode --no-install-workspace-modules

kill: # to be called from inside the devcontainer
	pgrep -f odoo-bin | xargs kill -s KILL

offline:
# Bootstrap, but without git clone/pull
	godoo launch --dev-mode --no-update-source

bare:
	GODOO_BOOSTRAP_ARGS="--no-install-workspace-modules" scripts/launchodoo.sh

reset:
# Deletes Devcontainer Volumes and Restarts devcontainer
	scripts/reset_devcontainer.sh

reset-hard: # To be called from Outside the Devcontainer
# Same as Reset, but additionally deletes VSCode Extension Volume and Force Rebuilds Container
	scripts/reset_devcontainer.sh --hard

rebuild:
	cd docker && docker compose -f docker-compose.base.yml -f docker-compose.devcontainer.yml build
