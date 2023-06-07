#!/bin/bash
# Checks requirements, for godoo container start
# Can create .env by itself

echo "==> Checking Requirements"
PROJ_FOLDER=$(dirname $(readlink -f $0))
PROJ_FOLDER=$(dirname $PROJ_FOLDER)

if [ -z "$SSH_AUTH_SOCK" ]; then
    echo "=x Make sure SSH agent is running. (SSH_AUTH_SOCK needs to be set)"
    exit 1
fi

if [ ! -f .env ]; then
    echo "==> No .env File detected. Copying default."
    cp .env.sample .env
fi
PROJ_NAME=$(basename $PROJ_FOLDER)
grep -q '^COMPOSE_PROJECT_NAME=' .env || echo "COMPOSE_PROJECT_NAME=$PROJ_NAME" >> .env

if [ ! -f "./docker/.env" ]; then
  echo "==> Symlinking .env to Docker folder"
  ln -s ../.env ./docker/.env
fi

TRAEFIK_NET=$(docker network ls --format="{{lower .Name}}" | grep traefik )
if [ -z "$TRAEFIK_NET" ]; then
  echo "=x Missing Traefik docker network."
  echo "==x Make sure traefik container is running and attached to network named 'traefik'"
  exit 1
fi

echo "==> Requrements check sucessfull"
