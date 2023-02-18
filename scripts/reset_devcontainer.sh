#!/bin/bash
# This script resets the Odoo environment.
# If executed from within the container, it just drops the DB and removes some files
# If executed from outside of the container, it will remove the Docker containers and volumes.
# Pass --hard to also delete Thirdparty addons and the devcontainer extension caches

PROJ_FOLDER=$(dirname $(readlink -f $0))
PROJ_FOLDER=$(dirname $PROJ_FOLDER)

source $PROJ_FOLDER/.env

[ "$1" = "--hard"  ] && RESET_ALL=true || RESET_ALL=false

remove_odoo_config() {
    conf_path="${ODOO_CONF_PATH:-config/odoo.conf}"
    echo "Deleting Config file: $conf_path"
    rm -f $conf_path
}

reset_docker () {
    # Get running Docker containers and Volumes and then delete them.
    [ -z "$COMPOSE_PROJECT_NAME" ] && COMPOSE_PROJECT_NAME=$(basename $PROJ_FOLDER)
    PROJ_NAME=$COMPOSE_PROJECT_NAME
    echo Deleting Containers with ProjName: $PROJ_NAME

    CONTAINERS=$(docker ps -a --format "{{.Names}}" | grep ^$PROJ_NAME)

    if [ "$RESET_ALL" = "true" ]; then
        VOLUMES=$(docker volume ls --format "{{.Name}}" | grep ^$PROJ_NAME)
    else
        VOLUMES=$(docker volume ls --format "{{.Name}}" | grep ^$PROJ_NAME | grep -Ev '(_vscode_cache$|_odoo_thirdparty$)')
    fi

    remove_odoo_config

    if [ ! -z "$CONTAINERS" ]; then
        echo Removing Devcontainers for Project: $PROJ_NAME ...
        docker rm -f $CONTAINERS
    fi

    if [ ! -z "$VOLUMES" ]; then
        echo Removing Devvolumes
        docker volume rm -f $VOLUMES
    fi

    if [ "$RESET_ALL" = "true" ]; then
        echo "Rebuilding Devcontainer Image"
        docker compose build --no-cache --parallel --pull
    fi
}

reset_native () {
    [ -z "$ODOO_DB_HOST" ] && echo "Env Var ODOO_DB_HOST missing" && exit 1
    [ -z "$ODOO_DB_PASSWORD" ] && echo "Env Var ODOO_DB_PASSWORD missing" && exit 1
    [ -z "$ODOO_DB_USER" ] && echo "Env Var ODOO_DB_USER missing" && exit 1
    [ -z "$ODOO_DB_PORT" ] && echo "Env Var ODOO_DB_PORT missing" && exit 1
    [ -z "$ODOO_MAIN_DB" ] && echo "Env Var ODOO_MAIN_DB missing" && exit 1

    set -e

    echo "Dropping DB if exist"
    PGPASSWORD=$ODOO_DB_PASSWORD dropdb -p $ODOO_DB_PORT -h $ODOO_DB_HOST -U $ODOO_DB_USER $ODOO_MAIN_DB --if-exists

    echo "Recreating DB"
    PGPASSWORD=$ODOO_DB_PASSWORD createdb -U $ODOO_DB_USER -h $ODOO_DB_HOST -p $ODOO_DB_PORT $ODOO_MAIN_DB --owner=$ODOO_DB_USER

    echo "Deleting Valib"
    sudo rm -rf /var/lib/odoo/*

    remove_odoo_config

    if [ "$RESET_ALL" = "true"  ] && [ ! -z $ODOO_THIRDPARTY_LOCATION ]; then
        rm -rf $ODOO_THIRDPARTY_LOCATION/*
    fi

}


if [ ! "$WORKSPACE_IS_DEV" = true ]  ; then
    read -p "This is not a Dev Env. Continue (y/n)?" choice
    case "$choice" in
    n|N ) exit 1;;
    y|Y ) echo Entering Dangerzone :O ;;
    * ) echo "invalid"; exit 1;;
    esac
fi

if [ ! -z $(which odoo-bin) ] # Check if Current Project folder matches .devcontainer workspacefolder
then
    reset_native
    exit 0
fi

reset_docker
