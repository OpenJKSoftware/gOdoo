.DEFAULT_GOAL := launch

launch: # To be called from inside the Devcontainer
# Bootstrap or Launch, Install/Upgrade Workspace addons, Keep running.
	python3 -m wodoo launch --dev-mode --odoo-demo

quick: # To be called from inside the Devcontainer
# Bootstrap or Launch, Install/Upgrade Workspace addons, Keep running.
	python3 -m wodoo launch --dev-mode --no-install-workspace-addons --no-update-source

kill: # to be called from inside the devcontainer
	kill $(ps aux | grep 'odoo-bin -' | awk '{print $2}')

offline:
# Bootstrap, but without git clone/pull
	python3 -m wodoo launch --dev-mode --no-update-source

bare:
	python3 -m wodoo launch --no-install-modules

reset:
# Deletes Devcontainer Volumes and Restarts devcontainer
	scripts/reset_devcontainer.sh

reset-hard: # To be called from Outside the Devcontainer
# Same as Reset, but additionally deletes VSCode Extension Volume and Force Rebuilds Container
	scripts/reset_devcontainer.sh --hard

stg:
	scripts/pull_remote_odoo_instance.sh
	python3 -m wodoo launch --prep-stage --dev-mode --no-install-modules --extra-bootstrap-args="-u all"

rebuild:
	DOCKER_BUILDKIT=1 docker build --ssh default --target devcontainer . --no-cache
