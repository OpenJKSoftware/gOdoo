#!/bin/bash

set -e
DIR=$(dirname $(readlink -f $0))

rm -f /etc/apt/apt.conf.d/docker-clean
apt-get update
apt-get install -y --no-install-recommends \
    lsb-release \
    gpg \
    wget \
    curl

# Add PostgreSQL repository and key
echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list
# Download the PostgreSQL key directly instead of using a local file
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | apt-key add -
apt-get update --fix-missing

# ######################### Install gOdoo dependencies #########################
apt-get install -y -q --no-install-recommends \
    git \
    apt-utils \
    openssh-client \
    rsync \
    unzip \
    postgresql-client-17 \
    libldap2-dev \
    libssl-dev \
    libsasl2-dev \
    libcups2-dev \
    build-essential \
    pkg-config
