# Downstream runtime setup

Downstream projects own their Dockerfile, Compose files, PostgreSQL sidecar, environment, runtime UID/GID, Make targets,
debug service, and image. The supported database topology is a same-host PostgreSQL sidecar on an isolated Compose
network. gOdoo manages selected sources and editor state.

## PostgreSQL 18 and pgvector

The representative stack runs `pgvector/pgvector:pg18-trixie` for Odoo 19 AI features. It enables the `vector` extension
in `template1` during first initialization, so databases created from the default template inherit it. Restored
databases carry their own extension state and can enable it with `CREATE EXTENSION IF NOT EXISTS vector` when needed.

PostgreSQL 18 stores its cluster below `/var/lib/postgresql/18/docker`, so the `db_data` volume mounts at
`/var/lib/postgresql`. The representative stack initializes this volume as a new PostgreSQL 18 cluster.

Compose mounts `config/postgresql.conf` read-only and starts PostgreSQL with that file. The conservative development
defaults allocate 256 MiB for vector-index maintenance and a 512 MiB shared-memory segment. Tune those values together
for larger production datasets.

## Prepare sources

From the project directory, set the required absolute `GODOO_SOURCES_ROOT`, then run `godoo workspace sync` and
`godoo workspace check`. `sync` may reset verified managed worktrees; `configure` generates the VS Code workspace after
checking local worktrees; `check` is offline and read-only. See [source workspaces](workspace.md).

Compose is downstream-owned. Before starting development, evaluate the shell-safe output from
`godoo workspace runtime-env` on the host. Combine the representative `docker-compose.base.yml` with
`docker-compose.dev.yml`; the development override mounts the project read-write and the complete source root read-only
at its existing absolute path, then forwards `GODOO_RUNTIME_ODOO_PATH` and `GODOO_RUNTIME_ADDON_PATHS`. Containers use
those paths directly and never inspect the mounted Git worktrees. Production uses the base file with the Traefik
override.

`make dev` starts PostgreSQL, runs initialization to completion, then starts the app, WebSocket service, and Nginx. Use
`make dev DEV_UP_ARGS=-d` to leave the stack running in the background. Nginx listens on host loopback port 8069.

## HTTP and file delivery

The representative stack sends ordinary HTTP through Nginx to Odoo on port 8069. Traefik routes `/websocket` to Odoo's
evented port 8072 in production, or to the separate `websocket` service in development. The loopback port 8069 also
points to Nginx; Odoo's HTTP port is not published.

`runtime init` enables Odoo's X-Sendfile support by default through `GODOO_X_SENDFILE`. After Odoo checks access to a
file-backed attachment, Nginx serves its `X-Accel-Redirect` from an internal filestore location. Nginx mounts the
existing `odoo_data` volume read-only. This volume contains more than the filestore; a dedicated filestore volume would
narrow Nginx's access but would require migrating existing files. Keep the Nginx location internal, and disable
X-Sendfile if a deployment removes Nginx.

PDF rendering fetches CSS through `report.url`. Set `GODOO_REPORT_URL` to the address of the file-serving proxy as seen
from the Odoo container; this Compose example defaults to `http://nginx`. `runtime init` stores the value in Odoo and
requires it when `GODOO_X_SENDFILE=true`.

Generated asset bundles stored as file-backed attachments use this path. Odoo still serves module files under
`/module/static/` because they sit outside the filestore. Nginx does not mount the host source trees, avoiding another
file-sharing path in development. Production sources are already copied into the Odoo image.

gOdoo finds third-party addons without a separate path setting. Development gets the manifest-selected paths from
`workspace runtime-env`. Production scans `thirdparty` beside the selected Odoo installation, where materialization puts
only selected repositories and archives.

## Build production image

Run `make prod` from the downstream project with Docker Compose 2.17 or newer. It first runs
`godoo workspace check --sources-only` on the host to verify the selected worktrees without requiring generated editor
files. Compose then passes `GODOO_SOURCES_ROOT` to BuildKit as the `godoo-sources` context. A separate materialization
stage runs `godoo workspace materialize` with the project and selected source root mounted read-only. It selects and
copies files and writes schema 2 provenance under `/image/odoo`; the host check owns worktree verification. The build
does not create a temporary host tree or run Git commands or worktree scans.

The production requirements layer bind-mounts only `/image/odoo/odoo/requirements.txt` from the materialized stage.
BuildKit keys this layer on the file contents, so gOdoo source edits can reuse the Odoo dependency install. The final
stage copies `/image/odoo/` into the image, then installs the gOdoo CLI package with normal dependency resolution.
`make prod` sets `GODOO_PACKAGE=/build/project` to install the checked-out CLI; downstream Compose retains the
`godoo-cli` default.

The production image sets `GODOO_RUNTIME_MATERIALIZED=1`, so runtime source resolution uses canonical image paths when
no explicit development source overrides are set. The resulting image is self-contained: do not mount
`GODOO_SOURCES_ROOT` into `init` or `app`. Canonical runtime paths are `/odoo/odoo`, `/odoo/godoo_workspace`,
`/odoo/thirdparty`, and `/odoo/config/odoo.conf`. Both runtime services share Odoo data and configuration volumes.
`init` runs `godoo runtime init` after PostgreSQL is healthy; `app` runs `godoo runtime launch` after initialization
succeeds.

Start downstream debug services before using the attach-only `gOdoo: attach` VS Code configuration. Do not bake database
passwords into images. See [runtime lifecycle](lifecycle.md) for state, locking, and recovery details.
