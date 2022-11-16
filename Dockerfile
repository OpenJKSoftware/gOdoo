# Odoo Docker Container
# This file uses Multiple Build Stages

ARG USERNAME=ContainerUser
ARG WORKSPACE=/odoo/workspace

FROM registry.gitlab.com/jksoftware1/docker-python:main as odoo_system_depends

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

FROM odoo_system_depends as node_npm
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
ARG USERNAME
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

# ------------------------------------------------------------------------------------------------------
# Install wodoo from this workspace.
# This workspace is made to be reused as a Odoo Workspace, Wodoo installed as a Package.
# Remove whats between the ----- and replace with: RUN pip install open-wodoo
COPY pyproject.toml poetry.lock ./
USER root
RUN set -x; \
    poetry config virtualenvs.create false \
    # A little bit of caching magic below
    # installs all dependencies on an empty package first since wodoo source changes more often than the files copied above
    # empty __init__.py gets overwritten by COPY below
    && mkdir ./open_wodoo && touch ./{open_wodoo/__init__.py,README.md} \
    && poetry install
# The following COPYs are below poetry install for better caching
COPY --chown=$USERNAME:$USERNAME open_wodoo open_wodoo
# Now install wodoo for real. Way faster now, because deps are already cached.
RUN poetry install
# In the Devcontainer stage we remove everything in $WORKSPACE and replace it with a Bind-Mount
# ---------------------------------------------------------------------------------------------------------

COPY --chown=$USERNAME:$USERNAME odoo_repospec.yml ./

# Install everything from Odoo requirements.txt, by downloading its raw contents
FROM python_workspace as odoo_requirements
RUN set -x; \
    wodoo get-source-file --file-path requirements.txt --save-path ./odoo_requirements.txt \
    && pip3 install -r ./odoo_requirements.txt --no-warn-script-location --disable-pip-version-check --upgrade

# Download Odoo Source Code
FROM python_workspace as odoo_source
RUN set -x ; \
    wodoo get-source --update-mode odoo \
    && chmod +x $ODOO_MAIN_FOLDER/odoo-bin

# Download Odoo Addons and unpack .zip Addons
FROM python_workspace as oodo_addon_source
COPY thirdparty thirdparty
RUN --mount=type=ssh set -x; \
    mkdir -p $ODOO_THIRDPARTY_LOCATION \
    && wodoo get-source --update-mode thirdparty \
    && wodoo get-source --update-mode zip

# Copy Addons and Odoo Source into image with requirements.
FROM odoo_requirements as base_odoo
ARG USERNAME
COPY --chown=${USERNAME}:${USERNAME} --from=odoo_source $ODOO_MAIN_FOLDER $ODOO_MAIN_FOLDER
COPY --chown=${USERNAME}:${USERNAME} --from=oodo_addon_source $ODOO_THIRDPARTY_LOCATION $ODOO_THIRDPARTY_LOCATION
EXPOSE 8069 8071 8072
RUN set -x; \
    mkdir -p {$ODOO_THIRDPARTY_LOCATION,$(dirname $ODOO_CONF_PATH),/var/lib/odoo} \
    && chown -R ${USERNAME}:${USERNAME} {$ODOO_THIRDPARTY_LOCATION,$(dirname $ODOO_CONF_PATH),/var/lib/odoo}
USER ${USERNAME}


# Image for Devserver (Start in Entrypoint)
FROM base_odoo as server
ARG USERNAME
RUN sudo rm -rf {/tmp/*,/var/cache/apt,/var/lib/apt/lists/*}
COPY --chown=${USERNAME}:${USERNAME} ./addons $ODOO_WORKSPACE_ADDON_LOCATION
ENTRYPOINT [ "/usr/local/bin/wodoo", "launch" ]


# Stage for testing, because we need the workspace with git available for delta checking
FROM base_odoo as test
ARG USERNAME
RUN sudo rm -rf {/tmp/*,/var/cache/apt,./*,/var/lib/apt/lists/*}
ADD --chown=${USERNAME}:${USERNAME} . .
ENTRYPOINT [ "/usr/local/bin/wodoo", "test" ,"all" ]


# Image for Devcontainer. (Infinite Sleep command for VScode Attach. Start Odoo via "Make")
FROM base_odoo as devcontainer
ARG USERNAME
USER root
COPY ./requirements.dev.txt .
VOLUME ["/home/${USERNAME}/.vscode-server"]
RUN --mount=type=cache,target=/var/cache/apt set -x; \
    sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt buster-pgdg main" > /etc/apt/sources.list.d/pgdg.list' \
    && wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | apt-key add - \
    && apt-get update \
    && apt-get -y install --no-install-recommends postgresql-client-15 netcat \
    && pip install -r ./requirements.dev.txt \
    && mkdir -p -m 0770 /home/${USERNAME}/.vscode-server/extensions \
    && chown -R ${USERNAME} /home/${USERNAME}/.vscode-server
COPY ./scripts/bash_completion /home/${USERNAME}/.bash_completions/wodoo.sh
# Separate statement, because it removes cache.
# We also remove everything in $workspace here, because we expect that to be mounted in in a devcontainer
RUN rm -rf {/tmp/*,/var/cache/apt,./*,/var/lib/apt/lists/*}

USER ${USERNAME}
RUN npm install -g prettier eslint
CMD [ "sleep", "infinity" ]
