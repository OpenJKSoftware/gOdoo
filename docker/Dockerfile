# syntax = docker/dockerfile:1.14
# Odoo Docker Container
# This file uses Multiple Build Stages

ARG USERNAME=ContainerUser

ARG WORKSPACE=/odoo/godoo_workspace
ARG VIRTUAL_ENV=/odoo/venv

ARG DEV_IMAGE=ghcr.io/openjksoftware/python-devcontainer:3.12
ARG PROD_IMAGE=python:3.12-slim-bullseye

ARG NODE_VERSION=20.18.0
ARG NVM_VERSION=v0.40.1
ARG SOURCE_CLONE_ARCHIVE=False


FROM ${DEV_IMAGE} AS dev_base
ARG WORKSPACE
WORKDIR $WORKSPACE

ENV ODOO_CONF_PATH=${WORKSPACE}/config/odoo.conf \
    ODOO_MAIN_FOLDER=/odoo/odoo \
    ODOO_WORKSPACE_ADDON_LOCATION=$WORKSPACE/addons \
    ODOO_MANIFEST=${WORKSPACE}/odoo_manifest.yml \
    ODOO_THIRDPARTY_LOCATION=/odoo/thirdparty \
    ODOO_THIRDPARTY_ZIP_LOCATION=${WORKSPACE}/thirdparty

ARG VIRTUAL_ENV
ARG USERNAME
# Path Env Vars:
ENV VIRTUAL_ENV=${VIRTUAL_ENV} \
    UV_PROJECT_ENVIRONMENT=${VIRTUAL_ENV} \
    UV_COMPILE_BYTECODE=1
ENV PYTHONPATH="$ODOO_MAIN_FOLDER:$WORKSPACE:$PYTHONPATH" \
    PATH="$ODOO_MAIN_FOLDER:$WORKSPACE:$WORKSPACE/scripts:$VIRTUAL_ENV/bin:$PATH"
RUN --mount=type=cache,target=$UV_CACHE_DIR,uid=1000,gid=1000 uv venv ${VIRTUAL_ENV}

ARG SOURCE_CLONE_ARCHIVE
ENV SOURCE_CLONE_ARCHIVE=${SOURCE_CLONE_ARCHIVE}

# Install Dependencies using depends_build.sh
RUN --mount=type=bind,source=docker/depends_build.sh,target=/tmp/depends_build.sh \
    set -ex; sudo bash /tmp/depends_build.sh

# ------------------------------------------------------------------------------------------------------
# Install godoo from this workspace. (see pip install below)
# This workspace is made to be reused as a Odoo Workspace, godoo installed as a Package.
# Remove whats between the ----- and replace with: RUN pip install godoo
# Install only dependencies, so they only reinstall when lock or toml where changed
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=$UV_CACHE_DIR,uid=1000,gid=1000 uv sync --no-install-project --compile-bytecode

COPY --chown=$USERNAME:$USERNAME ./src ./src
RUN --mount=type=cache,target=$UV_CACHE_DIR,uid=1000,gid=1000 uv sync --all-extras --compile-bytecode
# In the Devcontainer stage we remove everything in $WORKSPACE and replace it with a Bind-Mount
# ------------------------------------------------------------------------------------------------------
# Godoo Pypi Install via pip
# RUN pip install godoo-cli[devcontainer,codequality]
# ---------------------------------------------------------------------------------------------------------

COPY --chown=$USERNAME:$USERNAME odoo_manifest.yml ./

# Install everything from Odoo requirements.txt, by downloading its raw contents
FROM dev_base AS odoo_requirements
ARG ODOO_CONF_PATH
RUN --mount=type=cache,target=$UV_CACHE_DIR,uid=1000,gid=1000 set -ex; \
    godoo source get-file --file-path requirements.txt --save-path ./odoo_requirements.txt \
    && uv pip install -r ./odoo_requirements.txt --upgrade \
    && uv pip install pycups

# Download Odoo Source Code
FROM dev_base AS odoo_source
ARG ODOO_MAIN_FOLDER
RUN set -ex ; \
    godoo source get  odoo \
    && chmod +x $ODOO_MAIN_FOLDER/odoo-bin

# Download Odoo Addons and unpack .zip Addons
FROM dev_base AS oodo_addon_source
ARG USERNAME
RUN --mount=type=ssh set -ex; \
    mkdir -p $ODOO_THIRDPARTY_LOCATION \
    && sudo chown $USERNAME:$USERNAME $SSH_AUTH_SOCK || true \
    && godoo source get thirdparty
COPY thirdparty thirdparty
RUN godoo source get zip


# Image for Devserver (Start in Entrypoint)
FROM ${PROD_IMAGE} AS prod
ARG WORKSPACE
WORKDIR $WORKSPACE

ENV ODOO_CONF_PATH=/odoo/config/odoo.conf \
    ODOO_MAIN_FOLDER=/odoo/odoo \
    ODOO_WORKSPACE_ADDON_LOCATION=$WORKSPACE/addons \
    ODOO_MANIFEST=${WORKSPACE}/odoo_manifest.yml \
    ODOO_THIRDPARTY_LOCATION=/odoo/thirdparty \
    ODOO_THIRDPARTY_ZIP_LOCATION=${WORKSPACE}/thirdparty

# Path Env Vars:
ARG VIRTUAL_ENV
ENV VIRTUAL_ENV=$VIRTUAL_ENV
ENV PYTHONPATH="$ODOO_MAIN_FOLDER:$WORKSPACE:$PYTHONPATH" \
    PATH="$ODOO_MAIN_FOLDER:$WORKSPACE:$WORKSPACE/scripts:$VIRTUAL_ENV/bin:$PATH"

# Install runtime dependencies using depends_runtime.sh
RUN --mount=type=bind,source=docker/depends_runtime.sh,target=/tmp/depends_runtime.sh \
    set -ex; bash /tmp/depends_runtime.sh

COPY --from=odoo_source $ODOO_MAIN_FOLDER $ODOO_MAIN_FOLDER
COPY --from=oodo_addon_source $ODOO_THIRDPARTY_LOCATION $ODOO_THIRDPARTY_LOCATION
COPY --from=odoo_requirements $VIRTUAL_ENV $VIRTUAL_ENV

COPY ../config/nginx.conf /etc/nginx/nginx.conf
COPY ../config/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY ../scripts/launchodoo.sh launchodoo.sh
COPY ../scripts/migrations migrations

COPY --chown=$USERNAME:$USERNAME odoo_manifest.yml $ODOO_MANIFEST
ARG SOURCE_CLONE_ARCHIVE
ENV SOURCE_CLONE_ARCHIVE=${SOURCE_CLONE_ARCHIVE}

RUN set -ex; \
    mkdir -p /var/lib/odoo \
    && bash -c 'rm -rf {/tmp/*,/var/cache/apt,/var/lib/apt/lists/*}'
COPY ./addons $ODOO_WORKSPACE_ADDON_LOCATION

EXPOSE 8069 8071 8072
ENTRYPOINT ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]


# Copy Addons and Odoo Source into image with requirements.
FROM dev_base AS dev_odoo
# Install runtime dependencies and WKHTMLtoPDF
RUN --mount=type=bind,source=docker/depends_runtime.sh,target=/tmp/depends_runtime.sh \
    set -ex; sudo bash /tmp/depends_runtime.sh

ARG USERNAME
COPY --chown=${USERNAME}:${USERNAME} --from=odoo_source $ODOO_MAIN_FOLDER $ODOO_MAIN_FOLDER
COPY --chown=${USERNAME}:${USERNAME} --from=oodo_addon_source $ODOO_THIRDPARTY_LOCATION $ODOO_THIRDPARTY_LOCATION
COPY --chown=${USERNAME}:${USERNAME} --from=odoo_requirements $VIRTUAL_ENV $VIRTUAL_ENV
RUN set -ex; \
    sudo mkdir -p /var/lib/odoo \
    && sudo chown -R ${USERNAME}:${USERNAME} /var/lib/odoo

# Stage for testing, because we need the workspace with git available for delta checking
FROM dev_odoo AS test
ARG USERNAME
RUN set -ex; sudo bash -c 'rm -rf {/tmp/*,/var/cache/apt,./*,/var/lib/apt/lists/*}'
ADD --chown=${USERNAME}:${USERNAME} . .
ENTRYPOINT [ "godoo", "test" ,"all" ]


# Image for Devcontainer. (Infinite Sleep command for VScode Attach. Start Odoo via "Make")
FROM dev_odoo AS devcontainer

# Instal godoo autocomplete. Env var to disable shellingham, as docker doesnt run in a shell
RUN set -ex; _TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION=true godoo --install-completion zsh

ARG USERNAME
ARG NVM_VERSION
ARG NODE_VERSION
ENV NVM_DIR=/usr/local/nvm \
    NODE_VERSION=${NODE_VERSION}
RUN set -ex; sudo install -d -m 0755 -o ${USERNAME} -g ${USERNAME} $NVM_DIR

RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh | bash
# install node and npm
RUN set -ex; \
    . $NVM_DIR/nvm.sh \
    && nvm install $NODE_VERSION \
    && nvm alias default $NODE_VERSION \
    && npm config -g set cafile /etc/ssl/certs/ca-certificates.crt \
    && nvm use default
# add node and npm to path so the commands are available
ENV NODE_PATH=$NVM_DIR/v$NODE_VERSION/lib/node_modules \
    PATH=$NVM_DIR/versions/node/v$NODE_VERSION/bin:$PATH\
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

# Install PostgreSQL client and other development tools
RUN --mount=type=bind,source=docker/depends_build.sh,target=/tmp/depends_build.sh \
    set -ex; \
    sudo bash /tmp/depends_build.sh \
    && sudo bash -c 'mkdir -p /home/$USERNAME/{.vscode-server/extensions,.cache/pre-commit} \
    && chown -R $USERNAME:$USERNAME /home/$USERNAME/{.vscode-server,.cache} \
    && mkdir -p /etc/cups \
    && echo "ServerName cups:631" > /etc/cups/client.conf \
    && printf "Host upgrade.odoo.com\n\tStrictHostKeyChecking accept-new" > /etc/ssh/ssh_config.d/odoo_upgrade.conf'

ARG WORKSPACE
ENV ODOO_PRINT_PDF_LOCATION=$WORKSPACE

RUN set -ex; \
    sudo rm -rf /tmp/* \
    && sudo usermod -a -G www-data ${USERNAME} \
    && bash -c 'sudo chown -R ${USERNAME} {/var/lib/nginx,/var/log/nginx}'

RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=$UV_CACHE_DIR,uid=1000,gid=1000 \
    set -ex; \
    npm install -g prettier eslint \
    && git config --global --add safe.directory ${WORKSPACE}
ENV EDITOR=code
ENTRYPOINT  [ "/bin/sh", "-c", "sudo nginx -c /odoo/config/nginx.conf; sleep infinity"]
