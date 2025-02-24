#!/bin/bash
# This script resets the Odoo environment.
# If executed from within the container, it just drops the DB and removes some files
# If executed from outside of the container, it will remove the Docker containers and volumes.
# Pass --hard to also delete Thirdparty addons and the devcontainer extension caches

set -e

PROJ_FOLDER=$(dirname $0)
PROJ_FOLDER=$(readlink -f $(dirname $PROJ_FOLDER))

source $PROJ_FOLDER/.env

[ "$1" = "--hard"  ] && RESET_ALL=true || RESET_ALL=false

remove_odoo_config() {
    conf_path="${ODOO_CONF_PATH:-config/odoo.conf}"
    echo "==> Deleting Config file: $conf_path"
    rm -f $conf_path
}
remove_from_list() {
    # Remove a key from a space separated list
    ARRY=($1)
    KEY=$2
    for i in "${!ARRY[@]}"; do
        [[ ${ARRY[$i]} =~ $KEY ]] && unset ARRY[$i]
    done
    echo "${ARRY[*]}"
}

reset_docker () {
    # Get running Docker containers and Volumes and then delete them.
    [ -z "$COMPOSE_PROJECT_NAME" ] && COMPOSE_PROJECT_NAME=$(basename $PROJ_FOLDER)
    PROJ_NAME=$COMPOSE_PROJECT_NAME
    echo "==> Deleting Containers with ProjName: $PROJ_NAME"
    cd $PROJ_FOLDER/docker

    CONTAINERS=$(docker compose ps -a --format "{{.Names}}")
    remove_odoo_config
    if [ ! -z "$CONTAINERS" ]; then
        # Get Docker compose volume names
        VOLUMES=$(docker inspect $CONTAINERS --format "{{range .Mounts}}{{if .Name}}{{.Name}} {{end}}{{end}}")
        VOLUMES=$(remove_from_list "$VOLUMES" "^vscode$")

        if [ "$RESET_ALL" = "false" ]; then
            VOLUMES=$(remove_from_list "$VOLUMES" "_vscode_server$")
            VOLUMES=$(remove_from_list "$VOLUMES" "_commandhistory$")
            VOLUMES=$(remove_from_list "$VOLUMES" "_pre_commit_cache$")
        fi

        echo "==> Removing Devcontainers for Project: $PROJ_NAME"
        docker compose down
        if [ ! -z "$VOLUMES" ]; then
            echo "==> Removing Volumes: $VOLUMES"
            docker volume rm $VOLUMES || echo "==> Failed to remove Volume $VOLUMES"
        fi
    fi

    if [ "$RESET_ALL" = "true" ]; then
        # Act on all Available compose files here, to ensure that everything gets pulled and removed.
        export COMPOSE_FILE=$(ls -1 docker-compose*.yml | tr '\n' ':' | sed 's/:$//')
        echo "==> Pulling latest Docker Images"
        docker compose pull
        echo "==> Removing Docker Images"
        docker compose down -v
        rm -rf $PROJ_FOLDER/remote_instance_data/upgrade_extract
    fi

}

reset_native () {
    echo "==> Resetting Native"
    [ -z "$ODOO_DB_HOST" ] && echo "=x Env Var ODOO_DB_HOST missing" && exit 1
    [ -z "$ODOO_DB_PASSWORD" ] && echo "=x Env Var ODOO_DB_PASSWORD missing" && exit 1
    [ -z "$ODOO_DB_USER" ] && echo "=x Env Var ODOO_DB_USER missing" && exit 1
    [ -z "$ODOO_MAIN_DB" ] && echo "=x Env Var ODOO_MAIN_DB missing" && exit 1

    set -e

    # Common PostgreSQL connection parameters
    DB_PORT_PARAM=${ODOO_DB_PORT:+""-p $ODOO_DB_PORT""}
    DB_COMMON_ARGS="-h $ODOO_DB_HOST -U $ODOO_DB_USER $DB_PORT_PARAM"

    echo "==> Dropping DB if exist"
    PGPASSWORD=$ODOO_DB_PASSWORD dropdb $DB_COMMON_ARGS $ODOO_MAIN_DB --if-exists

    echo "==> Recreating DB"
    PGPASSWORD=$ODOO_DB_PASSWORD createdb $DB_COMMON_ARGS $ODOO_MAIN_DB --owner=$ODOO_DB_USER

    echo "==> Deleting Valib"
    if [ ! -n $RESET_KEEP_VARLIB ]; then
        sudo rm -rf /var/lib/odoo/*
    fi

    remove_odoo_config

    if [ "$RESET_ALL" = "true"  ] && [ ! -z $ODOO_THIRDPARTY_LOCATION ]; then
        echo "==> Clearing Thirdparty Volume"
        rm -rf $ODOO_THIRDPARTY_LOCATION/*
        rm -rf $PROJ_FOLDER/remote_instance_data/upgrade_extract
    fi

}


if [ ! "$WORKSPACE_IS_DEV" = true ]; then
    read -p "==> This is not a Dev Env. Continue (y/n)?" choice
    case "$choice" in
    n|N ) exit 1;;
    y|Y ) echo Entering Dangerzone :O ;;
    * ) echo "invalid"; exit 1;;
    esac
fi

if [ ! -z $(which odoo-bin) ]; then
    reset_native
    exit 0
fi

reset_docker
