# Repository architecture

`src/godoo_cli/` is the installable Python package. `commands/` contains Typer adapters and CLI option metadata;
`commands/root.py` registers the public commands. Keep command behavior in its owning domain module so that callers do
not need to import a CLI adapter to use it.

The main domain boundaries are:

| Directory    | Responsibility                                                                                                                                   |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `workspace/` | Select and manage shared source worktrees, generate editor state, and materialize selected sources for images. These operations run on the host. |
| `runtime/`   | Prepare and run Odoo against a selected database and filestore. Runtime code does not manage Git worktrees or containers.                        |
| `database/`  | PostgreSQL connections and database state.                                                                                                       |
| `git/`       | Repository and URL operations used by host workspace commands.                                                                                   |
| `models/`    | Configuration and value types shared across domains.                                                                                             |
| `helpers/`   | Small technical utilities without domain ownership.                                                                                              |

Dependencies should flow from `commands/` into the owning domain. Shared value types may be imported by more than one
domain; domain operations should not depend on command registration. Keep env-backed Typer option metadata in
`commands/common.py` and source-selection rules in the domain that owns them.

Root-level `tests/` exercises the package; its directories follow the same domain boundaries. Odoo addon tests stay
inside their addon under `addons/`. Use `make test` for the repository suite and the separate integration target only in
a prepared Odoo environment. Coverage measures `godoo_cli`, not test modules. The installed wheel is checked separately
because source-tree tests can miss packaging and entry-point errors.

`docker/` holds representative downstream build and Compose files, while `config/` holds configuration consumed by those
examples. Downstream projects own containers, system packages, service startup, and debugger startup. `docs/` explains
those contracts; `scripts/` holds repository maintenance commands.

Do not edit generated state by hand. The virtual environment, coverage output, `.godoo/`, generated VS Code workspaces,
and `pyrightconfig.json` are local artifacts. Use `godoo workspace configure` to regenerate editor files and
`godoo workspace check` to inspect them without changing source worktrees.
