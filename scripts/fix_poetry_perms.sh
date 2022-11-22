#!/bin/bash
# Since we install poetry without a venv, we need to fix some permissions in order to allow the user to use Poetry in the container.
# Since this is pretty brutal on permissions, please only use when neccessary.
CURRUSER=$(id -u -n)
PACKAGE_PATHS=$(python -c 'import site; print(" ".join(site.getsitepackages()))')
echo "Chowning: $PACKAGE_PATHS"
sudo chown -R $CURRUSER:$CURRUSER $PACKAGE_PATHS
