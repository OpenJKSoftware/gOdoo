---
name: godoo-source-workspaces
description:
  Work on gOdoo manifests, shared source roots, Git worktrees, source locks, archive caches, or generated editor
  workspace and debug files.
---

# gOdoo source workspaces

Read `docs/workspace.md` first. `odoo_manifest.yml` is authoritative and requires `odoo`; third-party prefixes are
validated before locks or paths and cannot alter host/container depth. Git owns pools, worktrees, branches, locks, and
archive caches below the shared source root. Never hand-edit managed worktrees or generated `.godoo/` and workspace
files.

Run `workspace sync` only with authority for the shared source root; it may reset verified managed worktrees.
`workspace configure` regenerates state from checked sources. `workspace check` is offline and read-only. Run workspace
commands from the project directory because that cwd supplies `.env` and manifest-relative paths.

Use the actual owning tests: `test_workspace_git.py` and `test_workspace_layout.py` for Git identity/confinement,
`test_workspace.py` for generated state, and `test_workspace_storage.py` for storage.
