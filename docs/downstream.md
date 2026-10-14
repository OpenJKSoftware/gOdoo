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

The launch commands read custom Odoo arguments from `ODOO_BIN_ARGS` in the host Compose `.env` file or shell. They
append proxy mode, and development also appends `--workers=0`. For container-only overrides, provide an explicit launch
command with the desired `--extra-args` values.

The example Nginx listens on host loopback port 8069 and is the only HTTP ingress. It proxies ordinary HTTP to
`app:8069` and `/websocket` to `app:8072`; development uses `app:8069` for both paths. Traefik routes all Odoo traffic
to Nginx. With `GODOO_X_SENDFILE=true`, Nginx serves file-backed attachments from the shared filestore. Keep its
file-serving location internal. A separate filestore volume could narrow Nginx access, but would require migrating
existing files. Disable X-Sendfile if the deployment removes Nginx.

Set `GODOO_REPORT_URL` to the file-serving proxy address as seen from Odoo (the example uses `http://nginx`). Init
stores this as Odoo's `report.url` and requires it when X-Sendfile is enabled.

Nginx also serves `/MODULE/static/FILE` directly, following
[Odoo's deployment guidance](https://www.odoo.com/documentation/19.0/administration/on_premise/deploy.html#serving-static-files-and-attachments).
The example sets `GODOO_NGINX_CONFIG_PATH=/odoo/config/nginx-static.conf`. Runtime init and launch write a module lookup
from the effective addon paths, including built-in and data-directory addons. The first matching module wins, even when
a later copy has a file the first copy lacks. Missing files fall back to Odoo. Static responses expire after 24 hours;
images receive `Content-Security-Policy: default-src 'none'`. Nginx enables sendfile and gzip for text assets.

Development mounts the project and shared source root read-only into Nginx at the same paths used by Odoo. Production
builds the `nginx` image target from the same materialized sources as the application; it needs no host source mounts.
Both modes mount the shared configuration and Odoo data volumes read-only. This gives Nginx access to the source trees;
only module static URLs and Odoo-authorized internal filestore redirects expose files over HTTP.

Use `--nginx-config PATH` on runtime init or launch to select another output, and update Nginx's include and mounts to
match. If you override addon paths, mount or copy those paths into Nginx too. Restart or reload Nginx after changing
module directories or addon selection; edits to existing static files are visible immediately, subject to browser
caching. When copying these examples downstream, include the Nginx template, image targets, and shared configuration
mount together.

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

## Select a migration transition

gOdoo owns transition-directory validation and converts `--migration-dir` (`GODOO_MIGRATION_DIR`) into existing init
inputs. Run from the downstream workspace root for relative paths, or provide an absolute mounted directory. Mount the
selected transition, native seed ZIP, and nonempty original filestore into the init environment. See
[migration directories](lifecycle.md#migration-directories) for the required files and reconciliation rules.

A downstream wrapper may forward its positional scripts directory as `--migration-dir`. It should leave version, script,
module-list, archive, and filestore checks to gOdoo. Pass intentional project hooks with explicit
`--after-reconcile-dir` options; they run after the transition's final hooks. Generic reconcile and phase-hook
environment defaults must be unset in migration mode, because init rejects conflicting defaults before mutation.
Downstream still owns Compose mounts, service ordering, and project shell scripts.

Selected inputs and dependency checks that can run before restore fail before mutation. Dependencies discovered only
from the restored database are checked afterward; a failure leaves the bound `after-restore` phase pending. Repair the
dependency and rerun the same selection to resume without reseeding. Finish any pending init started with direct flags
using those original flags before adopting `--migration-dir`.
