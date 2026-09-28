---
name: godoo-editor-validation
description:
  Validate gOdoo editor diagnostics, VS Code Problems, and debugpy attachment against resolved shared sources.
---

# gOdoo editor validation

Read `docs/workspace.md`, then open the generated `<project>.code-workspace`.

gOdoo enables Pylance and generates `python.analysis.extraPaths` from the resolved project, Odoo, third-party, and
archive addon roots. The installed Odoo IDE extension contributes Odoo-specific analysis but does not own Pylance's
import search path. The supported source mapping is the generated multi-root workspace plus its generated extra paths.

Reload the window after generation, save representative project, Odoo, and addon files, then inspect VS Code Problems.
Confirm each diagnostic refers to the intended selected host worktree. Problems validate editor analysis, not execution.

Inspect exact editor diagnostics in VS Code's Problems panel. Do not require a repository-specific extension or claim
that command-line lint and tests reproduce the aggregate Problems view. The Problems API is available only inside VS
Code's extension host, and Odoo IDE does not provide a supported external diagnostics transport. Record this UI check as
unverified when the active editor is unavailable.

Start the downstream debug service separately, attach with the generated `gOdoo: attach` configuration, set a breakpoint
in exercised code, and confirm the selected host/container mappings and local variables. Runtime startup belongs to
downstream/runtime validation.
