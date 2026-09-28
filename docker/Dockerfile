# syntax=docker/dockerfile:1.19
# Representative downstream image; copy and maintain it in the downstream project.
FROM pgvector/pgvector:pg18-trixie AS postgres

FROM python:3.12-slim-bookworm AS tooling
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv
COPY --from=postgres /usr/local/share/keyrings/postgres.gpg.asc /usr/local/share/keyrings/postgres.gpg.asc

RUN echo "deb [signed-by=/usr/local/share/keyrings/postgres.gpg.asc] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main 18" \
    > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates libpq-dev postgresql-client-18 \
    libldap2-dev libsasl2-dev libssl-dev libxml2-dev libxslt1-dev \
    libjpeg-dev zlib1g-dev libffi-dev liblcms2-dev libopenjp2-7-dev wkhtmltopdf \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/odoo-venv \
    UV_PROJECT_ENVIRONMENT=/opt/odoo-venv \
    PATH=/opt/odoo-venv/bin:/usr/lib/postgresql/18/bin:$PATH \
    GIT_PYTHON_REFRESH=quiet \
    GODOO_ODOO_REQUIREMENTS_PREINSTALLED=1 \
    UV_LINK_MODE=copy \
    ODOO_MAIN_FOLDER=/odoo/odoo \
    ODOO_WORKSPACE_ADDON_LOCATION=/odoo/godoo_workspace/addons \
    ODOO_CONF_PATH=/odoo/config/odoo.conf

ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid "$APP_GID" odoo \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --create-home odoo \
    && mkdir -p /odoo/godoo_workspace /odoo/config /odoo/thirdparty /var/lib/odoo \
    && chown -R odoo:odoo /odoo /var/lib/odoo

# Keep dependency resolution independent of gOdoo source edits.
FROM tooling AS dependencies
WORKDIR /build/project
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    --mount=type=bind,source=pyproject.toml,target=/build/project/pyproject.toml,readonly \
    --mount=type=bind,source=uv.lock,target=/build/project/uv.lock,readonly \
    uv sync --frozen --no-dev --no-install-project

# Development uses live project/source mounts and installs only Odoo requirements.
FROM dependencies AS development
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    --mount=type=bind,from=odoo-source,source=requirements.txt,target=/tmp/odoo-requirements.txt,readonly \
    uv pip install --python "$VIRTUAL_ENV/bin/python" -r /tmp/odoo-requirements.txt \
    && uv pip install --python "$VIRTUAL_ENV/bin/python" inotify \
    && chown -R odoo:odoo "$VIRTUAL_ENV"
WORKDIR /odoo/godoo_workspace
USER odoo
CMD ["python", "-m", "godoo_cli", "runtime", "launch"]

# Materialize selected sources from read-only mounts without Git commands or worktree scans.
FROM dependencies AS materialized
RUN --mount=type=bind,source=.,target=/build/project,readonly \
    --mount=type=bind,from=godoo-sources,source=.,target=/build/sources,readonly \
    PYTHONPATH=/build/project/src "$VIRTUAL_ENV/bin/python" -m godoo_cli workspace materialize \
        --project-root /build/project \
        --manifest /build/project/odoo_manifest.yml \
        --sources-root /build/sources \
        --destination-root /image

# This dependency layer keys on the requirements file contents alone.
FROM dependencies AS production-dependencies
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    --mount=type=bind,from=materialized,source=/image/odoo/odoo/requirements.txt,target=/tmp/odoo-requirements.txt,readonly \
    uv pip install --python "$VIRTUAL_ENV/bin/python" -r /tmp/odoo-requirements.txt \
    && chown -R odoo:odoo "$VIRTUAL_ENV"

# Install the CLI after dependency layers so application edits do not reinstall Odoo.
FROM production-dependencies AS production
ENV GODOO_RUNTIME_MATERIALIZED=1
ARG GODOO_PACKAGE=godoo-cli
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    --mount=type=bind,source=pyproject.toml,target=/build/project/pyproject.toml,readonly \
    --mount=type=bind,source=README.md,target=/build/project/README.md,readonly \
    --mount=type=bind,source=src,target=/build/project/src,readonly \
    uv pip install --python "$VIRTUAL_ENV/bin/python" "$GODOO_PACKAGE"
COPY --from=materialized --chown=odoo:odoo /image/odoo/ /odoo/
WORKDIR /odoo/godoo_workspace
USER odoo
CMD ["godoo", "runtime", "launch"]
