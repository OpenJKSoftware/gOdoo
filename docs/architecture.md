# Repository architecture

`src/godoo_cli/` is the installable Python package. `commands/` contains Typer adapters and option metadata;
`commands/root.py` registers public commands. Keep behavior in its owning domain so domain callers do not depend on CLI
registration.

| Package      | Owns                                                                                                   |
| ------------ | ------------------------------------------------------------------------------------------------------ |
| `workspace/` | Host-side source selection, managed worktrees, editor state, and image materialization.                |
| `runtime/`   | Odoo preparation and execution against a database and filestore. It does not manage Git or containers. |
| `database/`  | PostgreSQL connections and database state.                                                             |
| `git/`       | Repository URL operations used by host workspace commands.                                             |
| `models/`    | Configuration value types shared across domains.                                                       |
| `helpers/`   | Small utilities without domain ownership.                                                              |

Dependencies flow from `commands/` into a domain. Shared value types may cross domain boundaries; domain operations must
not depend on command registration. Keep environment-backed Typer metadata in `commands/common.py` and source-selection
rules in `workspace/`.

Tests under `tests/` follow the package domains; Odoo addon tests stay under `addons/`. `make test` runs the repository
suite. Odoo/PostgreSQL integration tests require a prepared environment and have a separate target. Coverage measures
`godoo_cli`, not test modules; the wheel is checked separately because source-tree tests do not catch packaging entry
point errors.

`docker/` holds representative downstream build and Compose files. `config/` holds configuration used by examples.
Downstream projects own containers, system packages, service startup, and debugger startup. See
[source workspaces](workspace.md) for generated editor files; do not hand-edit generated state such as `.godoo/`, VS
Code workspaces, or `pyrightconfig.json`.
