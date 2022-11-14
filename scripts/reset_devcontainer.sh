#!/bin/bash
# This Script needs to be executed from outside of the App container.
# Pass --hard to also delete Thirdparty addons and the devcontainer extension caches

PROJ_FOLDER=$(dirname $(readlink -f $0))
PROJ_FOLDER=$(dirname $PROJ_FOLDER)

source $PROJ_FOLDER/.env

conf_path="${ODOO_CONF_PATH:-config/odoo.conf}"

if [ "$PROJ_FOLDER" == "/odoo/workspace" ] # Check if Current Project folder matches .devcontainer workspacefolder
then

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

        echo "Deleting Config File $conf_path"
        rm -f $conf_path

        exit 0
fi

if [ ! "$WORKSPACE_IS_DEV" = true ]  ; then
    read -p "This is not a Dev Env. Continue (y/n)?" choice
    case "$choice" in
    n|N ) exit 1;;
    y|Y ) echo Entering Dangerzone :O ;;
    * ) echo "invalid"; exit 1;;
    esac
fi

[ -z "$COMPOSE_PROJECT_NAME" ] && echo "Env Var COMPOSE_PROJECT_NAME missing" && exit 1
PROJ_NAME=$COMPOSE_PROJECT_NAME
echo Deleting Containers with ProjName: $PROJ_NAME

CONTAINERS=$(docker ps -a --format "{{.Names}}" | grep ^$PROJ_NAME)

if [ "$1" = "--hard" ]
then
    VOLUMES=$(docker volume ls --format "{{.Name}}" | grep ^$PROJ_NAME)
    echo "Rebuilding Devcontainer Image"
    docker compose build --no-cache --parallel --pull
else
    VOLUMES=$(docker volume ls --format "{{.Name}}" | grep ^$PROJ_NAME | grep -Ev '(_vscode_cache$|_odoo_thirdparty$)')
fi

echo "Deleting Config file: $conf_path"
rm -f $conf_path

if [ ! -z "$CONTAINERS" ]
then
    echo Removing Devcontainers for Project: $PROJ_NAME ...
    docker rm -f $CONTAINERS
fi

if [ ! -z "$VOLUMES" ]
then
    echo Removing Devvolumes
    docker volume rm -f $VOLUMES
fi
