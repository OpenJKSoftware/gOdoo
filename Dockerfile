# Odoo Docker Container
# This file uses Multiple Build Stages

ARG USERNAME=ContainerUser
ARG WORKSPACE=/odoo/workspace

FROM registry.gitlab.com/jksoftware1/docker-python:main as odoo_depends

# Install Dependencies
USER root
RUN --mount=type=cache,target=/var/cache/apt set -x; \
    apt-get update \
    && apt-get -y install --no-install-recommends  \
    libxml2-dev \
    libxslt1-dev \
    libldap2-dev \
    libsasl2-dev \
    libtiff5-dev \
    libjpeg62-turbo-dev \
    libopenjp2-7-dev \
    zlib1g-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libxcb1-dev \
    libpq-dev \
    libssl-dev \
    fonts-noto-cjk \
    python3-dev \
    python3-libsass \
    graphviz \
    && curl -o wkhtmltox.deb -sSL https://github.com/wkhtmltopdf/wkhtmltopdf/releases/download/0.12.5/wkhtmltox_0.12.5-1.buster_amd64.deb \
    && echo 'ea8277df4297afc507c61122f3c349af142f31e5 wkhtmltox.deb' | sha1sum -c - \
    && apt-get install -y --no-install-recommends ./wkhtmltox.deb \
    && rm -rf wkhtmltox.deb

FROM odoo_depends as node_npm
ARG USERNAME
ENV NVM_DIR=/usr/local/nvm \
    NODE_VERSION=16.17.1
RUN install -d -m 0755 -o ${USERNAME} -g ${USERNAME} $NVM_DIR

USER ${USERNAME}
RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.1/install.sh | bash
# install node and npm
RUN source $NVM_DIR/nvm.sh \
    && nvm install $NODE_VERSION \
    && nvm alias default $NODE_VERSION \
    && npm config -g set cafile /etc/ssl/certs/ca-certificates.crt \
    && nvm use default
# add node and npm to path so the commands are available
ENV NODE_PATH=$NVM_DIR/v$NODE_VERSION/lib/node_modules \
    PATH=$NVM_DIR/versions/node/v$NODE_VERSION/bin:$PATH\
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt


# Adds Packages needed for this Odoo Workspace
FROM node_npm as python_workspace
ARG WORKSPACE
WORKDIR $WORKSPACE
# Wodoo Default Env Vars:
ENV ODOO_MAIN_FOLDER=/odoo/odoo \
    ODOO_GITSPEC=$WORKSPACE/odoo_repospec.yml \
    ODOO_THIRDPARTY_LOCATION=/odoo/thirdparty \
    ODOO_THIRDPARTY_ZIP_LOCATION=$WORKSPACE/thirdparty \
    ODOO_WORKSPACE_ADDON_LOCATION=$WORKSPACE/addons \
    ODOO_CONF_PATH=config/odoo.conf \
    ODOO_BOOTSTRAP_FLAG=/var/lib/odoo/bootstrap_flag \
    ODOO_MAIN_DB=odoo \
    ODOO_DB_FILTER=odoo \
    ODOO_DB_PASSWORD=odoo \
    ODOO_DB_USER=odoo_user \
    ODOO_DB_HOST=db \
    ODOO_DB_PORT=5432
# Path Env Vars:
ENV PYTHONPATH="$ODOO_MAIN_FOLDER:$WORKSPACE:$PYTHONPATH" \
    PATH="$ODOO_MAIN_FOLDER:$WORKSPACE:$WORKSPACE/scripts:/odoo/wodoo:$PATH"
ARG SOURCE_CLONE_ARCHIVE=False
ENV SOURCE_CLONE_ARCHIVE=${SOURCE_CLONE_ARCHIVE}

# Copy everything for wodoo to $WORKSPACE. Installs Wodoo from there.
# In the Devcontainer stage we remove all the files and replace the folder with a Bind-Mount
COPY odoo_repospec.yml pyproject.toml poetry.lock README.md $WORKSPACE/
COPY open_wodoo ${WORKSPACE}/open_wodoo
COPY thirdparty ${WORKSPACE}/thirdparty
USER root
RUN set -x; \
    chown -R $USERNAME:$USERNAME $WORKSPACE \
    && poetry config virtualenvs.create false \
    && poetry install


FROM python_workspace as odoo_requirements
ARG USERNAME
ARG WORKSPACE
USER ${USERNAME}
RUN set -x; \
    wodoo get-source-file --file-path requirements.txt --save-path $WORKSPACE/odoo_requirements.txt \
    && pip3 install -r $WORKSPACE/odoo_requirements.txt --no-warn-script-location --upgrade


FROM python_workspace as odoo_source
RUN set -x ; \
    wodoo get-source --update-mode odoo \
    && chmod +x $ODOO_MAIN_FOLDER/odoo-bin


FROM python_workspace as oodo_addon_source
RUN --mount=type=ssh set -x; \
    mkdir -p $ODOO_THIRDPARTY_LOCATION \
    && wodoo get-source --update-mode thirdparty \
    && wodoo get-source --update-mode zip

# Copies Source to image and installs Odoo Depends.
FROM odoo_requirements as base_odoo
ARG USERNAME
ARG WORKSPACE
COPY --chown=${USERNAME}:${USERNAME} --from=odoo_source $ODOO_MAIN_FOLDER $ODOO_MAIN_FOLDER
COPY --chown=${USERNAME}:${USERNAME} --from=oodo_addon_source $ODOO_THIRDPARTY_LOCATION $ODOO_THIRDPARTY_LOCATION
EXPOSE 8069 8071 8072
USER root
RUN set -x; \
    mkdir -p {$ODOO_THIRDPARTY_LOCATION,$(dirname $ODOO_CONF_PATH),/var/lib/odoo} \
    && chown -R ${USERNAME}:${USERNAME} {$ODOO_THIRDPARTY_LOCATION,$(dirname $ODOO_CONF_PATH),/var/lib/odoo}
USER ${USERNAME}


# Image for Devserver (Start in Entrypoint)
FROM base_odoo as server
ARG USERNAME
ARG WORKSPACE
USER root
RUN rm -rf {/tmp/*,/var/cache/apt}
COPY --chown=${USERNAME}:${USERNAME} ./addons $ODOO_WORKSPACE_ADDON_LOCATION
USER ${USERNAME}
ENTRYPOINT [ "wodoo launch --conf-path ${ODOO_CONF_PATH}" ]


# Stage for testing, because we need the workspace with git available for delta checking
FROM base_odoo as test
ARG USERNAME
ARG WORKSPACE
ADD --chown=${USERNAME}:${USERNAME} . $WORKSPACE
USER ${USERNAME}
ENTRYPOINT [ "wodoo test all" ]


# Image for Devcontainer. (Infinite Sleep command for VScode Attach. Start Odoo via "Make")
FROM base_odoo as devcontainer
ARG USERNAME
ARG WORKSPACE
USER root
COPY ./requirements.dev.txt $WORKSPACE
VOLUME ["~/.vscode-server"]
RUN --mount=type=cache,target=/var/cache/apt set -x; \
    sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt buster-pgdg main" > /etc/apt/sources.list.d/pgdg.list' \
    && wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | apt-key add - \
    && apt-get update \
    && apt-get -y install --no-install-recommends postgresql-client-15 netcat \
    && pip install -r $WORKSPACE/requirements.dev.txt \
    && mkdir -p -m 0770 ~/.vscode-server/extensions \
    && chown -R ${USERNAME} ~/.vscode-server
# Separate statement, because it removes cache.
# We also remove everything in $workspace here, because we expect that to be mounted in in a devcontainer
RUN rm -rf {/tmp/*,/var/cache/apt,$WORKSPACE/*,/var/lib/apt/lists/*}

USER ${USERNAME}
RUN npm install -g prettier eslint
CMD [ "sleep", "infinity" ]
