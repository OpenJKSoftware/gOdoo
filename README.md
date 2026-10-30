<div align="center">

# gOdoo

**Odoo source workspaces, runtime lifecycle, and developer tooling.**

From shared Git worktrees to database restores, native migrations, and a running Odoo instance.

[![Quality](https://github.com/OpenJKSoftware/gOdoo/actions/workflows/quality.yml/badge.svg?branch=main)](https://github.com/OpenJKSoftware/gOdoo/actions/workflows/quality.yml)
[![Latest release](https://img.shields.io/github/v/release/OpenJKSoftware/gOdoo?label=release)](https://github.com/OpenJKSoftware/gOdoo/releases/latest)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: LGPL v3](https://img.shields.io/badge/license-LGPL_v3-blue)](LICENSE)

[Quick start](#quick-start) · [Commands](#commands) · [Documentation](#documentation) · [Contributing](#contributing)

</div>

---

gOdoo is a Python CLI for teams developing and operating Odoo projects. It manages manifest-selected sources, prepares
databases with their matching filestores, and runs Odoo against that prepared state.

Git, workspace commands, and editor configuration run on the host. Runtime commands run in the selected Python
environment. Your downstream project owns Docker, system packages, runtime users, service ordering, and debugger
startup.

> [!NOTE]
>
> This README describes the development branch. The workspace and runtime interfaces below have changed since
> [v0.17.0](https://github.com/OpenJKSoftware/gOdoo/releases/tag/v0.17.0). Follow the source checkout instructions to
> try them, or read the [released README](https://github.com/OpenJKSoftware/gOdoo/blob/v0.17.0/README.md) when using
> that release.

## What you can do

| Area                      | Capabilities                                                                                                               |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Source workspaces**     | Select Odoo and addon repositories through a manifest; share managed Git worktrees and generate VS Code configuration.     |
| **Runtime lifecycle**     | Initialize, reconcile, inspect, and launch Odoo with explicit commands and dependency preflight.                           |
| **Database & filestore**  | Back up and restore matching pairs, load fixed datasets, overlay upgraded archives, and reuse development reset baselines. |
| **Migrations & hooks**    | Use native Odoo migrations for versioned repairs and phase-based hooks for module requests and environment setup.          |
| **Dependencies & builds** | Plan addon Python requirements from source manifests and install them into an explicit interpreter.                        |
| **Daily development**     | Run Odoo tests, open shells, execute scripts, and manage a running instance through RPC.                                   |

Runtime commands require **Odoo 16 or newer**. CI checks gOdoo on **Python 3.11 and 3.12**; the example Odoo 19 stack
uses **PostgreSQL 18 with pgvector**.

## Quick start

### Run the example development stack

Install [Git](https://git-scm.com/), [uv](https://docs.astral.sh/uv/), Python 3.11 or newer, Make, Docker, and Docker
Compose. Then clone the development branch and install the host tools:

```bash
git clone https://github.com/OpenJKSoftware/gOdoo.git
cd gOdoo
make setup
cp .env.sample .env
```

Edit `.env` before continuing:

- Set `GODOO_SOURCES_ROOT` to an absolute directory **outside this checkout**, accessible to Docker.
- Set the database credentials and target values for your environment.

Review [`odoo_manifest.yml`](odoo_manifest.yml) to select Odoo and any third-party sources, then run:

```bash
make sync       # Synchronize managed source worktrees.
make workspace  # Generate the host editor workspace.
make dev        # Build and start the example Compose stack.
```

`make sync` changes managed worktrees and may reset verified worktrees. `make workspace` generates editor configuration;
`make dev` validates source paths on the host before starting Compose.

The example stack uses Nginx for HTTP and file delivery. See [downstream runtime setup](docs/downstream.md) for image,
networking, restore dataset mounts, and production build configuration.

### Use gOdoo in your own project

Install the CLI from the development branch:

```bash
uv tool install "git+https://github.com/OpenJKSoftware/gOdoo.git@main"
```

On Linux, the PostgreSQL Python driver also requires PostgreSQL development headers and a C compiler. Your runtime
environment needs the Odoo sources, Python dependencies, PostgreSQL tools, and system packages described in the
[downstream guide](docs/downstream.md).

Run workspace commands from your project's directory, with its own `.env` and `odoo_manifest.yml`. Run runtime commands
in the Python environment that contains Odoo; gOdoo uses `VIRTUAL_ENV`, otherwise the project's `.venv`. Follow the
[source workspace guide](docs/workspace.md) to configure sources and the editor.

## Commands

| Command           | Purpose                                                                                          |
| ----------------- | ------------------------------------------------------------------------------------------------ |
| `godoo workspace` | Synchronize, validate, inspect storage, and materialize selected sources; generate editor files. |
| `godoo runtime`   | Initialize and launch Odoo, report runtime status, plan dependencies, and open shells.           |
| `godoo db`        | Back up, restore, reset, and inspect databases and their filestores.                             |
| `godoo test`      | Run Odoo tests, load test data, and find changed modules.                                        |
| `godoo rpc`       | Manage modules, configuration parameters, imports, and translations over RPC.                    |
| `godoo upgrade`   | Forward arguments to Odoo's official upgrade client.                                             |
| `godoo run`       | Run `odoo-bin` with explicit arguments in the selected runtime environment.                      |

Commands read the project `.env`. **Explicit CLI values → exported environment → project `.env`** is the configuration
precedence. Use `godoo --help` or `godoo <command> --help` for the full reference.

### Prepare on every server start

Initialization prepares the database and matching filestore, checks dependencies, and runs selected module updates and
hooks. `runtime start` performs this work before launching Odoo on the same target. Use it as the app entrypoint so
Docker restarts also prepare the runtime; the app depends on a healthy database.

```bash
# Run in a configured runtime environment with other Odoo writers stopped.
godoo runtime start --update-changed
```

`--update-changed` selects native upgrades from source hashes; unchanged starts skip upgrades. Explicit maintenance
`runtime init` and prepared-only `runtime launch` remain available. `runtime init --if-needed` skips all hooks and
reconciliation on a ready runtime; `runtime start` always initializes explicitly. Inspect state without changing it:

```bash
godoo runtime status --json
```

See the [lifecycle guide](docs/lifecycle.md) for dataset selection, demo mode, restore progress, reset baselines, and
recovery. Use [native migrations and hooks](docs/hooks.md) for versioned repairs and recurring environment policy.

## Documentation

| Guide                                           | Start here when you need to…                                                         |
| ----------------------------------------------- | ------------------------------------------------------------------------------------ |
| [Source workspaces](docs/workspace.md)          | Select shared sources, generate editor configuration, or configure host development. |
| [Runtime lifecycle](docs/lifecycle.md)          | Initialize, restore, upgrade, reset, or recover a database and filestore.            |
| [Lifecycle hooks](docs/hooks.md)                | Request modules, write reconciliation hooks, or integrate native Odoo migrations.    |
| [Downstream runtime setup](docs/downstream.md)  | Configure Docker images, Compose services, PostgreSQL, Nginx, and dataset storage.   |
| [Repository architecture](docs/architecture.md) | Find the code and tests responsible for a behavior.                                  |

## Migrating from earlier versions

The host workspace and explicit runtime commands replace the former Dev Container workflow. Downstream projects now own
their Dockerfile and Compose configuration.

<details>
<summary><strong>Legacy command reference</strong></summary>

| Previous command or surface                            | Current equivalent                                |
| ------------------------------------------------------ | ------------------------------------------------- |
| Repository Dev Container startup                       | Downstream-owned Dockerfile and Compose           |
| `godoo source get`                                     | `godoo workspace sync`, then `godoo runtime init` |
| `godoo source sync-conf`                               | `godoo runtime init`                              |
| `godoo deployment-init`, `ensure-runtime`, `bootstrap` | `godoo runtime init`                              |
| `godoo reconcile-runtime`                              | `godoo runtime init --update` or `--install`      |
| `godoo launch`, `shell`, `shell-script`                | Corresponding `godoo runtime` command             |
| `godoo reset`                                          | `godoo db reset`                                  |
| Generated source Compose or provenance files           | `godoo workspace configure` and live inspection   |

Other `godoo source` inspection and dependency helpers are retired. Inspect checked-out worktrees directly; runtime
preflight checks Python dependencies. Use native Odoo migrations and lifecycle hooks for upgrade work.

</details>

## Contributing

Bug reports and focused pull requests are welcome. [Open an issue](https://github.com/OpenJKSoftware/gOdoo/issues) with
the gOdoo revision, Odoo version, command, and relevant diagnostics so the behavior can be reproduced.

For development, follow the source checkout steps above. Install the pinned text formatter with `npm ci`, then open
`godoo.code-workspace` so the repository's editor settings apply. See [repository architecture](docs/architecture.md)
for code ownership and test layout.

Run the host quality checks and repository tests:

```bash
make lint
make test
```

Real Odoo/PostgreSQL integration tests use a prepared runtime environment and the separate `make test-odoo-integration`
target. See [runtime lifecycle](docs/lifecycle.md) and [downstream setup](docs/downstream.md) before running them.

## License & acknowledgements

gOdoo is licensed under the [GNU Lesser General Public License v3.0](LICENSE).

Made possible by [WEMPE Elektronic GmbH](https://wetech.de).
