# gOdoo

gOdoo provides host-side Odoo tooling for source workspaces, database lifecycle, automation, and RPC operations.
Downstream projects own Docker, system packages, runtime users, service startup, and debugger startup.

Commands read the project `.env` without overriding exported variables. Explicit CLI values take precedence over the
environment, which takes precedence over `.env`. Run `godoo --help` or `godoo <command> --help` for the command
reference.

## Start a development environment

Install Git, uv, Python 3.11 or newer, Docker, and Docker Compose. Then configure the project:

```bash
make setup
cp .env.sample .env
# Set GODOO_SOURCES_ROOT and the required database values in .env.
make sync
make workspace
make dev
```

CI covers Python 3.11 and 3.12. The representative Odoo 19 stack uses PostgreSQL 18; see [downstream runtime setup](docs/downstream.md).

`make sync` is the step that changes managed source worktrees. `make workspace` generates editor configuration on the
host. `make dev` validates sources on the host before downstream Compose starts. See [source workspaces](docs/workspace.md)
for setup details and [downstream runtime setup](docs/downstream.md) for the image and service contract.

## Guides

- [Runtime lifecycle](docs/lifecycle.md): database and filestore state, initialization, upgrades, and recovery.
- [Repository architecture](docs/architecture.md): code, tests, and ownership boundaries.
- [Source workspaces](docs/workspace.md): manifest-selected sources and generated editor configuration.
- [Downstream runtime setup](docs/downstream.md): Compose, PostgreSQL, HTTP delivery, and production images.

## Command migration

| Previous command or surface | Current equivalent |
| --- | --- |
| Repository Dev Container startup | Downstream-owned Dockerfile and Compose |
| `godoo source get` | `godoo workspace sync`, then `godoo runtime init` |
| `godoo source sync-conf` | `godoo runtime init` |
| `godoo deployment-init`, `ensure-runtime`, `bootstrap` | `godoo runtime init` |
| `godoo reconcile-runtime` | `godoo runtime init --update` or `--install` |
| `godoo launch`, `shell`, `shell-script` | Corresponding `godoo runtime` command |
| `godoo reset` | `godoo db reset` |
| Generated source Compose or provenance files | `godoo workspace configure` and live inspection |

Other `godoo source` inspection and dependency helpers are retired. Inspect checked-out worktrees directly; runtime
preflight checks Python dependencies.

Made possible by [WEMPE Elektronic GmbH](https://wetech.de).
