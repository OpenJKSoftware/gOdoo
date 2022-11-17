.DEFAULT_GOAL := launch

launch: # To be called from inside the Devcontainer
# Bootstrap or Launch, Install/Upgrade Workspace addons, Keep running.
	wodoo launch --dev-mode --odoo-demo

enterprise:
	wodoo launch --dev-mode --odoo-demo --load-data-path ./odoo_import_scripts/install_enterprise.py

quick: # To be called from inside the Devcontainer
# Bootstrap or Launch, Install/Upgrade Workspace addons, Keep running.
	wodoo launch --dev-mode --no-install-workspace-addons --no-update-source

kill: # to be called from inside the devcontainer
	kill $(ps aux | grep 'odoo-bin -' | awk '{print $2}')

offline:
# Bootstrap, but without git clone/pull
	wodoo launch --dev-mode --no-update-source

bare:
	wodoo launch --no-install-modules

reset:
# Deletes Devcontainer Volumes and Restarts devcontainer
	scripts/reset_devcontainer.sh

reset-hard: # To be called from Outside the Devcontainer
# Same as Reset, but additionally deletes VSCode Extension Volume and Force Rebuilds Container
	scripts/reset_devcontainer.sh --hard

stg:
	scripts/pull_remote_odoo_instance.sh
	wodoo launch --prep-stage --dev-mode --no-install-modules --extra-bootstrap-args="-u all"

rebuild:
	DOCKER_BUILDKIT=1 docker build --ssh default --target devcontainer . --no-cache
