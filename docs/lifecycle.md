# Runtime lifecycle CLI

gOdoo manages an Odoo **runtime**: one PostgreSQL database and its matching filestore. It adds safe, project-oriented
lifecycle orchestration around `odoo-bin`; it does not replace Odoo's general-purpose CLI.

The canonical command family is `godoo runtime`. During the transition, the existing top-level commands remain supported
as compatibility aliases (see [Migration](#migration)). All commands honour the usual environment-first configuration,
including `ODOO_MAIN_DB` and `ODOO_DATA_DIR`.

## Choose a command

| Goal                                                                   | Canonical command         | What it may change                                                    |
| ---------------------------------------------------------------------- | ------------------------- | --------------------------------------------------------------------- |
| Prepare source, configuration, and Python dependencies                 | `godoo runtime prepare`   | Source checkout, config, Python environment; never runtime state      |
| Create only a missing/empty runtime                                    | `godoo runtime bootstrap` | Database and matching filestore only when bootstrap is required       |
| Apply deliberate module/state policy to a ready runtime                | `godoo runtime reconcile` | Explicit module actions and hooks; never restore, bootstrap, or reset |
| Select seed, bootstrap, or reconcile based on runtime state, then exit | `godoo runtime init`      | As selected by its state matrix                                       |
| Start an already prepared runtime                                      | `godoo runtime launch`    | Nothing before starting Odoo                                          |
| Inspect whether a runtime is missing, empty, ready, or unhealthy       | `godoo runtime status`    | Nothing                                                               |

`runtime init` is intended for a one-shot Compose/Kubernetes init job. `runtime launch` belongs in the long-running
application process.

## Runtime lifecycle

```text
prepare    source sync (when requested) -> config -> Python dependencies
bootstrap  initialize only a missing or empty runtime (requires prior preparation)
reconcile  prepare -> optional installed-dependency resolution -> explicit install/update/upgrade -> after-reconcile hooks
init       status -> seed or bootstrap or no-op -> matching phase hooks -> reconcile -> after-reconcile hooks -> exit
launch     start only
```

Pass `--sync-sources` (or `GODOO_PREPARE_SYNC_SOURCES=true`) to `runtime prepare` to synchronize the configured
`ODOO_MANIFEST` and `ODOO_THIRDPARTY_ZIP_LOCATION` before writing configuration and resolving dependencies.
`runtime bootstrap` deliberately omits those preparation steps; use `runtime init` when one command should perform both.

`reconcile` is the steady-state deployment operation. It does not infer an upgrade from changed source code: pass the
intended actions explicitly, for example:

```bash
godoo runtime reconcile --sync-sources --update my_module --install another_module
```

The relevant reconciliation controls are:

| Control                            | Environment variable                  | Meaning                                                   |
| ---------------------------------- | ------------------------------------- | --------------------------------------------------------- |
| `--sync-sources`                   | `GODOO_RECONCILE_SYNC_SOURCES`        | Synchronize manifest-controlled source before reconciling |
| `--resolve-installed-dependencies` | `GODOO_RECONCILE_DEPENDENCIES`        | Resolve Python dependencies of installed Odoo modules     |
| `--update MODULES`                 | `GODOO_RECONCILE_UPDATE`              | Module updates; repeat the option or use commas           |
| `--install MODULES`                | `GODOO_RECONCILE_INSTALL`             | Module installs; repeat the option or use commas          |
| `--upgrade-path PATH`              | `GODOO_RECONCILE_UPGRADE_PATH`        | Additional Odoo upgrade path                              |
| `--pre-upgrade-script PATH`        | `GODOO_RECONCILE_PRE_UPGRADE_SCRIPTS` | Odoo pre-upgrade scripts; repeat the option               |
| `--log-handler MODULE:LEVEL`       | `GODOO_RECONCILE_LOG_HANDLERS`        | Odoo log handlers; repeat the option or use commas        |
| `--x-sendfile` / `--no-x-sendfile` | `GODOO_X_SENDFILE`                    | Persist the file-delivery policy during preparation       |
| `--after-reconcile-dir DIR`        | `GODOO_AFTER_RECONCILE_DIRS`          | Ordered Odoo-shell hook directories after reconciliation  |

`runtime init` accepts the same reconciliation controls plus seed and bootstrap controls. A ready runtime is reconciled;
it is never replaced by init. In particular, `GODOO_RECONCILE_DEPENDENCIES=true` resolves Python dependencies for
installed modules before its explicit module actions.

### Init state matrix

| Runtime state          | Seed supplied | `runtime init` action                                                             |
| ---------------------- | ------------- | --------------------------------------------------------------------------------- |
| Ready                  | Either        | Skip the seed, reconcile, then run after-reconcile hooks                          |
| Missing or empty       | Yes           | Load the seed, run after-restore hooks, reconcile, then run after-reconcile hooks |
| Missing or empty       | No            | Bootstrap, run after-bootstrap hooks, reconcile, then run after-reconcile hooks   |
| Unhealthy/inconsistent | Either        | Fail; repair or deliberately replace it                                           |

The seed control is `--seed` / `GODOO_RUNTIME_SEED`. It accepts Odoo's native filestore-aware ZIP format or a legacy
directory containing `odoo.dump` and `odoo_filestore`. ZIP loads delegate to `odoo-bin`; legacy restores validate and
stage both artifacts before replacement. A seed is an initialization input, not a request to overwrite a ready runtime.
`--seed-archive` / `GODOO_SEED_ARCHIVE` remains a deprecated ZIP-compatible alias.

## Storage operations

Storage commands always operate on a database-and-filestore pair. The default portable artifact is Odoo's
filestore-aware ZIP archive.

| Goal                                         | Canonical command                               | Notes                                                                 |
| -------------------------------------------- | ----------------------------------------------- | --------------------------------------------------------------------- |
| Create portable archive                      | `godoo runtime storage archive create`          | Delegates to Odoo's native database/filestore archive flow            |
| Load an archive or gOdoo 0.17 dump directory | `godoo runtime storage archive load`            | Restores the selected database and matching filestore                 |
| Make an ordinary runtime copy                | `godoo runtime storage clone SOURCE TARGET`     | Uses Odoo's filestore-aware database duplication                      |
| Make a copy-on-write runtime copy            | `godoo runtime storage clone-cow SOURCE TARGET` | Requires PostgreSQL 18+ and verified reflink support for both volumes |
| Remove a runtime                             | `godoo runtime storage drop`                    | Drops the database and matching filestore                             |

The archive loader accepts Odoo ZIP archives and the directory format emitted by gOdoo 0.17: `odoo.dump` plus
`odoo_filestore`. Legacy dumps copied the full Odoo data directory, so the loader restores the selected database's
matching filestore. If several filestores are present, use the original database name to make the selection unambiguous.
Loading a legacy directory requires `--force` because it replaces the target runtime. Native ZIPs without `filestore/`
members are valid for databases whose filestore is empty; `dump.sql` remains mandatory.

`clone-cow` is deliberately exceptional. It fails closed when either volume does not provide a real reflink; it never
degrades to an unannounced full copy and is not a backup. See [the CoW workflow](cow.md).

## Phase hooks

Phase hooks are downstream application policy. Configure repeatable directories with `--after-bootstrap-dir`,
`--after-restore-dir`, and `--after-reconcile-dir`, or their plural `GODOO_AFTER_*_DIRS` environment variables.
Directories execute in supplied order; each directory's direct `*.py` children execute through Odoo shell in lexical
filename order. After-reconcile hooks run for bootstrapped, restored, and already-ready runtimes.
`--pre-launch-hooks-dir` / `GODOO_PRE_LAUNCH_HOOKS_DIR` remains a deprecated alias for one after-reconcile directory.

Each file runs in a separate Odoo shell session. Odoo rolls back the open shell transaction on exit, so mutating hooks
must call `env.cr.commit()` explicitly; commits made by earlier files are not rolled back if a later hook fails. A
failure stops the init operation. The complete directory is attempted again on the next init, so every hook must be
idempotent.

These hooks are not Odoo module migrations. Schema and module data migrations belong in Odoo's module migration
mechanisms; pre-launch hooks are for deployment policy that needs an initialized and reconciled Odoo environment.

## Compose pattern

Run state changes in a short-lived init service and keep the application service start-only:

```yaml
services:
  runtime-init:
    command: godoo runtime init --update my_module
  app:
    depends_on:
      runtime-init:
        condition: service_completed_successfully
    command: godoo runtime launch
```

For initialization seed artifacts, make their mounted paths explicit:

```yaml
services:
  runtime-init:
    command: >-
      godoo runtime init --seed /seed/runtime.zip --update my_module
  app:
    depends_on:
      runtime-init:
        condition: service_completed_successfully
    command: godoo runtime launch
```

Compose YAML, volumes, artifact production/transport, module policy, hook contents, and process/reverse-proxy policy
remain the downstream project's responsibility.

## Safety guarantees

- `runtime launch` does not prepare, bootstrap, reconcile, restore, reset, or otherwise alter runtime state before
  starting Odoo.
- `runtime reconcile` never restores, bootstraps, or resets a runtime.
- Bootstrap creates state only when Odoo reports it missing or empty.
- Seed inputs never replace a ready runtime. Archive load, clone, and drop remain explicit storage commands.
- Native archives are validated before a forced load, and archive creation replaces the destination only after success.
- Database and filestore are treated as one runtime unit. `ODOO_DATA_DIR` (or `--data-dir`) is the filestore storage
  authority.
- gOdoo delegates ordinary database/filestore operations to `odoo-bin` where Odoo owns them; the custom dump path adds
  validation and safe staging only.

## Live container tests

The regular pytest suite uses isolated unit tests and does not require Odoo or PostgreSQL. From a running gOdoo
development container, run the opt-in live suite with:

```bash
make test-odoo-integration
```

The live suite starts short-lived Odoo commands against UUID-named temporary databases and a pytest temporary data
directory. It verifies lifecycle hook ordering, repeat initialization of an existing runtime, template reset, and empty
reset across both the database and filestore. It never uses the configured `ODOO_MAIN_DB`, and a prefix-guarded
finalizer removes its temporary databases even when an assertion fails.

## Migration

The following existing commands remain supported while the canonical surface is adopted. Prefer the canonical form in
new Compose files and automation.

| Existing command                  | Canonical command                                                                          |
| --------------------------------- | ------------------------------------------------------------------------------------------ |
| `godoo prepare`                   | `godoo runtime prepare`                                                                    |
| `godoo bootstrap`                 | `godoo runtime bootstrap` (the canonical command only initializes a missing/empty runtime) |
| `godoo reconcile-runtime`         | `godoo runtime reconcile`                                                                  |
| `godoo deployment-init`           | `godoo runtime init`                                                                       |
| `godoo ensure-runtime`            | `godoo runtime init` when no seed is configured                                            |
| `godoo launch`                    | `godoo runtime launch`                                                                     |
| `godoo db dump` / `godoo db load` | `godoo runtime storage archive create` / `godoo runtime storage archive load`              |
| `godoo backup dump DIR`           | `godoo runtime storage archive create DIR/runtime.zip`                                     |
| `godoo db duplicate-cow`          | `godoo runtime storage clone-cow`                                                          |
| `godoo reset --empty`             | `godoo runtime storage drop`                                                               |

`godoo reset --db-template` remains an explicit template-replacement utility during migration. Do not map it to ordinary
bootstrap or reconcile operations. The deprecated seed and pre-launch hook options emit migration guidance when used.

## Use `odoo-bin` directly for Odoo utilities

gOdoo intentionally does not wrap every Odoo CLI feature. Use `odoo-bin` directly for module utilities outside explicit
lifecycle reconciliation, i18n, neutralization, scaffolding, population, cloc, obfuscation, deployment utilities, and
code upgrades (`upgrade_code`). This boundary keeps gOdoo a small lifecycle helper while Odoo remains the authority for
Odoo-specific operations.
