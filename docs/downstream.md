# Downstream runtime setup

Downstream projects own the Dockerfile, Compose files, PostgreSQL sidecar, runtime UID/GID, environment, service
startup, debug service, and image. gOdoo manages selected sources and editor configuration. The supported database
topology is a same-host PostgreSQL sidecar on an isolated Compose network.

## Prepare sources and start development

From the downstream project directory, set the absolute `GODOO_SOURCES_ROOT`, then run `godoo workspace sync` and
`godoo workspace check`. Sync may reset verified managed worktrees; check is offline and read-only. See
[source workspaces](workspace.md) for the manifest and editor contract.

The representative development stack combines `docker-compose.base.yml` and `docker-compose.dev.yml`. Before Compose
starts, resolve selected source paths on the host with `godoo workspace runtime-env`. The development override mounts
the project read-write and the complete source root read-only at its existing absolute `GODOO_SOURCES_ROOT` path.
`GODOO_RUNTIME_ODOO_PATH` names the selected Odoo subpath; `GODOO_RUNTIME_ADDON_PATHS` carries selected addon paths.
Containers do not inspect or manage Git worktrees.

## PostgreSQL

The representative Odoo 19 stack uses `pgvector/pgvector:pg18-trixie` for AI features. It enables `vector` in
`template1`, so new databases inherit the extension. Restored databases may need
`CREATE EXTENSION IF NOT EXISTS vector`. PostgreSQL 18 stores its cluster under `/var/lib/postgresql/18/docker`; mount
the database volume at `/var/lib/postgresql`.

Compose mounts `config/postgresql.conf` read-only. Its development defaults allocate 256 MiB for vector-index
maintenance and a 512 MiB shared-memory segment; tune both for larger datasets.

## HTTP and file delivery

The example Nginx listens on host loopback port 8069 and proxies ordinary HTTP to Odoo. In production, Traefik routes
`/websocket` directly to Odoo on port 8072; the development stack uses a separate WebSocket service. With
`GODOO_X_SENDFILE=true`, Nginx serves file-backed attachments from the shared filestore. Keep its file-serving location
internal. A separate filestore volume could narrow Nginx access, but would require migrating existing files. Disable
X-Sendfile if the deployment removes Nginx.

Set `GODOO_REPORT_URL` to the file-serving proxy address as seen from Odoo (the example uses `http://nginx`). Init
stores this as Odoo's `report.url` and requires it when X-Sendfile is enabled. Odoo serves module static files itself;
they are outside the filestore. Do not mount host source trees into Nginx.

## Build and run production images

Run `make prod` with Docker Compose 2.17 or newer. It checks selected worktrees on the host with
`godoo workspace check --sources-only`, then passes `GODOO_SOURCES_ROOT` as the BuildKit `godoo-sources` context. A
read-only materialization stage copies only selected repositories into `/image/odoo` and writes schema-2 source
provenance. Builds do not create temporary host source trees or run Git against worktrees inside the image build.

The requirements layer bind-mounts only `/image/odoo/odoo/requirements.txt`. The image includes the checked-out CLI and
materialized runtime sources; it is self-contained. Runtime paths are `/odoo/odoo`, `/odoo/godoo_workspace`,
`/odoo/thirdparty`, and `/odoo/config/odoo.conf`. Do not mount `GODOO_SOURCES_ROOT` into `init` or `app`, and do not
bake database passwords into the image.

The `init` service runs `godoo runtime init` after PostgreSQL is healthy. The `app` service runs `godoo runtime launch`
only after init completes successfully (`service_completed_successfully`). Start downstream debug services before using
the attach-only `gOdoo: attach` VS Code configuration.

For Odoo's upgrade client and restore workflow, see the [runtime lifecycle guide](lifecycle.md). The optional
`scripts/odoo_instance_get_upgrade.sh` wrapper belongs to the downstream project, not gOdoo. It maps:

| Downstream variable           | Official client option |
| ----------------------------- | ---------------------- |
| `ODOO_DUMP_SQL_PATH`          | `-i` input dump        |
| `ODOO_UPGRADE_TARGET_VERSION` | `-t` target version    |
| `ODOO_ENTERPRISE_SUBCODE`     | `-c` contract          |

The wrapper also passes `-x` to suppress the official client's local restore.
