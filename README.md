# gOdoo

gOdoo manages shared Git worktrees, prepares Python dependencies, and runs Odoo and PostgreSQL workflows. Downstream projects own Docker, system packages, runtime users, service orchestration, and debugger startup.

## CLI features

- Source workspaces synchronize and inspect manifest-selected Odoo and
  third-party Git worktrees, manage archive caches, generate the VS Code
  workspace, and materialize selected sources into production images.
- Runtime lifecycle commands initialize, launch, inspect, and open shells in an Odoo
  runtime while preserving the database and filestore lifecycle boundary.
- Database operations prepare, back up, restore, clone, reset, inspect, and
  query PostgreSQL databases used by Odoo.
- Odoo automation runs `odoo-bin`, executes tests, loads test data, and detects
  changed modules.
- RPC workflows install modules, import data, manage configuration
  parameters, and export translations through Odoo's RPC API.

Commands accept environment-backed options and load the project `.env` without
overwriting values already exported by the caller. Run `godoo --help` or
`godoo <command> --help` for the complete command reference. See
[source workspaces](docs/workspace.md),
[downstream runtime setup](docs/downstream.md), and
[runtime lifecycle](docs/lifecycle.md) for operational details.

See [repository architecture](docs/architecture.md) for code and test ownership.


## Repository onboarding

Install Git, UV, Python 3.11 or newer, Docker, and Docker Compose. CI covers Python 3.11 and 3.12. Then run:

```bash
make setup
cp .env.sample .env
# Mandatory: set GODOO_SOURCES_ROOT and the required database values in .env.
make sync
make workspace
```

The configured project `.env` is required for tests and operational commands.
`ODOO_MANIFEST` defaults to `odoo_manifest.yml` in the project directory.
`make setup` installs the host development environment. `make sync` is the only
step that changes managed source worktrees. After those sources exist, `make
workspace` runs gOdoo on the host and writes the VS Code workspace file. `make
dev` validates and resolves source paths on the host before Compose starts, so
runtime containers never run Git against bind-mounted worktrees.

Run `make lint` and `make test` before committing changes.

## Migrating to 1.0

| Previous surface | Replacement |
| --- | --- |
| Repository Dev Container and service startup | Downstream-owned Dockerfile and Compose |
| `godoo source get` | `godoo workspace sync`, then `godoo runtime init` for runtime preparation |
| `godoo source sync-conf` | `godoo runtime init` |
| `godoo deployment-init`, `ensure-runtime`, or `bootstrap` | `godoo runtime init` |
| `godoo reconcile-runtime` | `godoo runtime init --update` or `--install` |
| `godoo launch`, `shell`, or `shell-script` | The corresponding `godoo runtime` command |
| `godoo reset` | `godoo db reset` |
| Generated source Compose/provenance files | `godoo workspace configure` and live inspection |

The other `godoo source` inspection and dependency helpers are retired. Inspect checked worktrees directly and use runtime preflight for Python dependencies.

Made possible by [WEMPE Elektronic GmbH](https://wetech.de).
