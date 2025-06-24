#!/bin/bash

set -e
DIR=$(dirname $(readlink -f $0))

rm -f /etc/apt/apt.conf.d/docker-clean
apt-get update
apt-get install -y --no-install-recommends \
    lsb-release \
    gpg

echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list
mv $DIR/psql.gpg /etc/apt/trusted.gpg.d/
apt-get update --fix-missing

# ######################### Install gOdoo dependencies #########################
apt-get install -y -q --no-install-recommends \
    git \
    apt-utils \
    openssh-client \
    wget \
    rsync \
    curl \
    unzip \
    postgresql-client-17 \
    libldap2-dev \
    libssl-dev \
    libsasl2-dev \
    build-essential \
    pkg-config \
    libcups2-dev
