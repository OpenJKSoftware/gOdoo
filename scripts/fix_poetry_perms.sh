#!/bin/bash
# Since we install poetry without a venv, we need to fix some permissions in order to allow the user to use Poetry in the container.
# Since this is pretty brutal on permissions, please only use when neccessary.
CURRUSER=$(id -u -n)
sudo chown -R $CURRUSER:$CURRUSER {/usr/local/src/, /usr/local/lib/python3.*}
