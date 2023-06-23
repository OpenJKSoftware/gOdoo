.DEFAULT_GOAL := launch

launch: # To be called from inside the Devcontainer
# Bootstrap or Launch, Install/Upgrade Workspace addons, Keep running.
	godoo source get
	godoo launch --dev-mode --odoo-demo --multithread-worker-count=4

quick: # To be called from inside the Devcontainer
# Bootstrap or Launch, Install/Upgrade Workspace addons, Keep running.
	godoo launch --dev-mode --no-install-workspace-addons --no-update-source

kill: # to be called from inside the devcontainer
	kill $(ps aux | grep 'odoo-bin -' | awk '{print $2}')

offline:
# Bootstrap, but without git clone/pull
	godoo launch --dev-mode --no-update-source

bare:
	godoo launch --no-install-modules

reset:
# Deletes Devcontainer Volumes and Restarts devcontainer
	scripts/reset_devcontainer.sh

reset-hard: # To be called from Outside the Devcontainer
# Same as Reset, but additionally deletes VSCode Extension Volume and Force Rebuilds Container
	scripts/reset_devcontainer.sh --hard

rebuild:
	DOCKER_BUILDKIT=1 docker build --ssh default --target devcontainer . --no-cache
