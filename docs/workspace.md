# Source workspaces

Run workspace commands from the downstream project directory. gOdoo reads that project's `.env` without overriding
exported values. `GODOO_SOURCES_ROOT` must be an absolute shared-source path outside the project. `ODOO_MANIFEST`
defaults to `odoo_manifest.yml` there; the manifest is the source-selection authority and must declare Odoo plus any
third-party repositories.

| Command                       | Effect                                                                                                     |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `godoo workspace sync`        | Changes managed Git worktrees and archive caches; may reset verified worktrees.                            |
| `godoo workspace configure`   | Checks selected sources and generates editor files; it serializes generation with `.godoo/workspace.lock`. |
| `godoo workspace check`       | Offline, read-only validation of sources, with no source-worktree or archive locks.                        |
| `godoo workspace runtime-env` | Validates sources on the host and prints shell-safe resolved path assignments.                             |

The generated `<project>.code-workspace` uses the fixed folder name `gOdoo` and `${workspaceFolder:gOdoo}` for project
paths. Selected Odoo, addon, and archive paths remain absolute host paths. Pylance receives them through
`python.analysis.extraPaths`; the generated `gOdoo: attach` configuration maps the project to `/odoo/godoo_workspace`
and uses the selected source paths inside the development container.

Downstream Compose owns bind mounts and service configuration. Development resolves paths on the host and mounts the
source root read-only; runtime commands do not inspect Git. Production builds check sources on the host, then
materialize selected files from a read-only BuildKit context. See [downstream runtime setup](downstream.md) for those
contracts.

Configure removes retired `.godoo/docker-compose.sources.yml` and `.godoo/source-provenance.json` only when they are
regular files. Check fails while either remains. Configure also generates the ignored project-root `pyrightconfig.json`
for the project `.venv` (Python 3.11), including `src/`, `addons/`, `scripts/`, project addons, and resolved source
paths. Run `make workspace` before `make typecheck`; typecheck runs `.venv/bin/pyright --project pyrightconfig.json` and
fails if the generated file is missing. `make configure` remains a compatibility alias for `make workspace`.
