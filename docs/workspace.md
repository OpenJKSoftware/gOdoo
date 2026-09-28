# Source workspaces

Run workspace commands from the downstream project directory. gOdoo loads that directory's required `.env` without
overriding the process environment. `GODOO_SOURCES_ROOT` is an absolute shared-source-root path outside the project.
`ODOO_MANIFEST` defaults to `odoo_manifest.yml` in the Project dir; the manifest is the sole source-selection authority.

`workspace sync` is the only command that changes managed Git worktrees or archive caches. `workspace configure` and
`workspace check` inspect existing selected sources without creating repository or archive locks. Configure writes
`<project>.code-workspace` and retains `.godoo/workspace.lock` to serialize workspace generation; check is offline and
read-only.

The generated workspace lists the project as `.` under the fixed name `gOdoo`. It uses `${workspaceFolder:gOdoo}` for
project paths in workspace settings, tasks, and launch configurations. These paths stay tied to the project folder,
including when the workspace is generated inside the development container. Selected Odoo, third-party, and archive
roots remain absolute host paths. Pylance receives those paths through `python.analysis.extraPaths`; the generated
`gOdoo: attach` configuration maps the project to `/odoo/godoo_workspace` and selected sources to the same absolute
paths inside the development container.

Downstream Compose owns services, bind mounts, BuildKit contexts, users, and cache mounts. Before development starts,
`workspace runtime-env` validates the selected worktrees on the host and prints shell-safe resolved path assignments.
Development mounts the full source root read-only at the same absolute path and passes those paths to the containers;
runtime commands do not inspect Git. Production `make prod` first runs `workspace check --sources-only` on the host,
then supplies `GODOO_SOURCES_ROOT` as a BuildKit context. A separate image stage runs `workspace materialize` against
read-only project and source mounts, then writes selected contents and provenance beneath `/image/odoo`. The final image
copies these files into canonical `/odoo` paths. Materialization selects and copies sources; the host check verifies
worktrees. No temporary host source tree is used, and no Git commands or worktree scans run inside the image build.

Use `make workspace` to generate the editor workspace on the host. `make configure` remains a compatibility alias.

`.godoo/docker-compose.sources.yml` and `.godoo/source-provenance.json` are retired artifacts. Configure removes safe
regular-file copies; check fails while either remains, preventing old generated infrastructure from being used.

Configure also generates the ignored project-root `pyrightconfig.json` alongside `<project>.code-workspace`. It targets
the project `.venv` with Python 3.11, includes `src/`, `addons/`, and `scripts/`, and adds the project addon directory
plus resolved source and archive paths to `extraPaths`. Run `make workspace` before `make typecheck`; the latter runs
`.venv/bin/pyright --project pyrightconfig.json` and fails if the generated configuration is missing.
