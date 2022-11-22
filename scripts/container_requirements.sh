#!/bin/bash

if [ -z "$SSH_AUTH_SOCK" ]; then
    echo "Make sure SSH agent is running. (SSH_AUTH_SOCK needs to be set)"
    exit 1
fi

if [ ! -f .env ]; then
    echo "No .env File detected. Copying default."
    cp .env.sample .env
fi

TRAEFIK_NET=$(docker network ls --format="{{lower .Name}}" | grep traefik )
if [ -z "$TRAEFIK_NET" ]; then
  echo "Missing Traefik docker network."
  echo "Make sure traefik container is running and attached to network named 'traefik'"
  exit 1
fi
