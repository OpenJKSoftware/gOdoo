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
`GODOO_RUNTIME_ODOO_PATH` names the selected Odoo subpath; `GODOO_RUNTIME_ADDON_PATHS` carries selected addon paths. Git
inspection and worktree management run on the host.

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
builds the `nginx` image target from the same materialized sources as the application. Both modes mount the shared
configuration and Odoo data volumes read-only. This gives Nginx access to the source trees; only module static URLs and
Odoo-authorized internal filestore redirects expose files over HTTP.

Use `--nginx-config PATH` on runtime init or launch to select another output, and update Nginx's include and mounts to
match. If you override addon paths, mount or copy those paths into Nginx too. Restart or reload Nginx after changing
module directories or addon selection; edits to existing static files are visible immediately, subject to browser
caching. When copying these examples downstream, include the Nginx template, image targets, and shared configuration
mount together.

## Build and run production images

Run `make prod` with Docker Compose 2.17 or newer. It checks selected worktrees on the host with
`godoo workspace check --sources-only`, then passes `GODOO_SOURCES_ROOT` as the BuildKit `godoo-sources` context. A
read-only materialization stage copies only selected repositories into `/image/odoo` and writes schema-2 source
provenance.

The requirements layer bind-mounts only `/image/odoo/odoo/requirements.txt`. Install manifest-declared Python
dependencies during an image build with the materialized source layout and an explicit target interpreter:

```bash
godoo runtime dependencies --odoo-path /odoo/odoo \
  --workspace-addons /odoo/godoo_workspace/addons --install --python /odoo/venv/bin/python
```

Use repeated `--module` to select particular addons, or omit it to select installable workspace addons compatible with
the selected Odoo major. Repeated `--requirements` adds migration or upgrade requirement files. Without `--install`, the
command prints a deterministic requirements artifact. `GODOO_RUNTIME_ODOO_PATH`, path-separated
`GODOO_RUNTIME_ADDON_PATHS`, and `GODOO_RUNTIME_WORKSPACE_ADDONS` provide the same source path inputs.

The image includes the checked-out CLI and materialized runtime sources; it is self-contained. Runtime paths are
`/odoo/odoo`, `/odoo/godoo_workspace`, `/odoo/thirdparty`, and `/odoo/config/odoo.conf`. Do not mount
`GODOO_SOURCES_ROOT` into `init` or `app`, and do not bake database passwords into the image.

The `init` service runs `godoo runtime init` after PostgreSQL is healthy. The `app` service runs `godoo runtime launch`
only after init completes successfully (`service_completed_successfully`). Start downstream debug services before using
the attach-only `gOdoo: attach` VS Code configuration.

## Restore dataset storage

gOdoo reads paths visible to its process. Downstream Compose files decide whether those paths are host bind mounts or
Docker volumes; setting `GODOO_DUMP_PATH` alone does not create, mount, or move storage.

Keep the matching database and original attachments together:

```text
<dataset>/
├── odoo.dump
├── upgraded.zip
└── odoo_filestore/filestore/odoo/
```

Plain init selects `odoo.dump` and its original filestore. Adding `--migration-dir` selects `upgraded.zip` and overlays
its new attachments on the original filestore. Keep each upgraded ZIP paired with the backup submitted to the upgrade
service. Do not mount or rely on a stale `upgrade_extract` directory.

| Setting                                             | Owner and meaning                                                                                                                                                                                                                     |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GODOO_DUMP_PATH` / `--dump-path`                   | gOdoo dataset directory inside the running process, for example `/odoo_restore`. A downstream wrapper may also use the host value as an import source or production bind-mount source, then override it inside the container.         |
| `GODOO_RUNTIME_SEED` / `--seed`                     | Explicit ZIP input; takes precedence over automatic dataset selection. Usually unnecessary with the paired layout above.                                                                                                              |
| `GODOO_ORIGINAL_FILESTORE` / `--original-filestore` | Explicit original attachment directory. Otherwise inferred from the paired dataset when its upgraded ZIP is selected.                                                                                                                 |
| `GODOO_DUMP_VOLUME`                                 | Downstream Compose setting, not a gOdoo CLI option. The Wetech development stack defaults to the external volume `godoo-restore-datasets`.                                                                                            |
| `TMPDIR`                                            | Temporary-file location inherited by `godoo upgrade` and the official client. The Wetech upgrade job sets it to `/odoo_restore/.upgrade` so temporary work stays in the dataset volume. Runtime init has its own process environment. |
| `GODOO_SSH_AUTH_SOCK`, `GODOO_SSH_KNOWN_HOSTS`      | Wetech tooling-container mount settings for the host SSH agent socket and a read-only `known_hosts` file. These are not gOdoo runtime options.                                                                                        |

In the Wetech downstream, `make import-dump` copies an existing host directory into the volume once. Later `make pull`,
`make upgrade`, and review-transfer commands write directly to that volume; `make dev` reads it. `make prod` continues
reading its host `GODOO_DUMP_PATH` bind mount. These are separate copies: updating the development volume does not
update the production host directory. Stage the matching dataset in the directory that the selected runtime actually
mounts.

A one-off Compose `dataset` service provides writable volume access for imports, SSH/rsync transfers, and the official
upgrade client without starting PostgreSQL or Odoo. It exits after its command and is removed with `run --rm`; it is not
another daemon or a gOdoo requirement. Keep restore mounts read-only. Reusing init for these jobs would require
overriding its mounts, user, command, and dependencies on every invocation. A tooling service can use a CLI/tools image
without Odoo sources, addon dependencies, or debugger packages; the current Wetech wrapper instead builds its full
development image if missing, so even an import can require source setup and an application dependency build.

### Docker performance and lifetime

On macOS and Windows, host bind mounts cross the Docker VM's file-sharing boundary. Large filestores amplify the cost
through many small file and metadata operations. Importing both the dump and extracted original filestore once into a
native Linux volume avoids repeating that host transfer on later restores; keeping only the ZIP there leaves original
attachment copies crossing the boundary. Pulling and upgrading directly in the volume also avoids staging their output
on the host. See Docker's [volume guidance](https://docs.docker.com/engine/storage/volumes/) and
[host-to-VM file-sharing explanation](https://docs.docker.com/desktop/features/synchronized-file-sharing/).

A volume does not guarantee reflinks or a particular speedup. When source and destination support cloning on the same
filesystem, gOdoo clones attachment data and later writes allocate private blocks. Otherwise it falls back to rsync,
then a Python copy if rsync is unavailable. Separate Docker volumes may share a backing filesystem; their names alone do
not establish clone support. Use gOdoo's copy-method logs to check the actual path. Native Linux bind mounts do not have
the same desktop VM boundary, so a named volume is not automatically faster there. ZIP decompression, PostgreSQL
restore, and reconciliation still take time; moving storage does not remove those phases.

Use an explicitly named external source volume so `compose down --volumes` retains it. Keep each project's database and
runtime filestore in separate volumes. The source consumes persistent disk space alongside runtime and reset-baseline
data; logical directory sizes can count shared reflink extents more than once. External volumes survive project teardown
but are not backups. Downstream owns volume names, SSH forwarding, UID/GID ownership, imports, and writer coordination.
Do not update a shared dataset while another job is writing it or another project is restoring from it.

For Odoo's upgrade client and restore workflow, see [the runtime lifecycle guide](lifecycle.md). The downstream
`make upgrade` target calls `godoo upgrade test` with the dataset's `odoo.dump`, `ODOO_UPGRADE_TARGET_VERSION`, and
`ODOO_ENTERPRISE_SUBCODE`, plus `-x` to suppress the official client's local restore. This does not change an existing
runtime database.

## Select a migration transition

gOdoo owns transition-directory validation and converts `--migration-dir` (`GODOO_MIGRATION_DIR`) into existing init
inputs. Run from the downstream workspace root for relative paths, or provide an absolute mounted directory. Mount the
selected transition, native seed ZIP, and nonempty original filestore into the init environment. See
[migration directories](lifecycle.md#migration-directories) for the required files and reconciliation rules.

A downstream wrapper may forward its positional scripts directory as `--migration-dir`. It should leave version, script,
module-list, archive, and filestore checks to gOdoo. Pass intentional project hooks with explicit
`--after-reconcile-dir` options; they run after the transition's final hooks. Generic reconcile and phase-hook
environment defaults must be unset in migration mode, because init rejects conflicting defaults before mutation.
Downstream still owns Compose mounts, service ordering, and project shell scripts. gOdoo launch applies the bundled
banner when `ODOO_BANNER_TEXT` or `ODOO_BANNER_BG_COLOR` is non-empty, independently of development mode.

Selected inputs and dependency checks that can run before restore fail before mutation. Dependencies discovered only
from the restored database are checked afterward; a failure leaves init incomplete. Repair the dependency and rerun
init. Hooks and reconciliation run again, so project hooks must be safe to rerun.
