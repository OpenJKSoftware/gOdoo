---
name: godoo-editor-validation
description: Verify gOdoo editor diagnostics, navigation readiness, and debugger attachment.
---

# gOdoo editor validation

Use `docs/workspace.md` for configuration. Open `godoo.code-workspace` here or the generated `<project>.code-workspace`
downstream. Recheck the active window and selected source worktree after user interaction. Keep the Python extension for
interpreter selection and debugging.

Inspect exact diagnostics in VS Code Problems; CLI lint and tests do not reproduce that aggregate view. Mark editor
validation unverified when the intended window is unavailable. Do not invent an external Odoo IDE diagnostics transport.

For authorized attachment checks, use `gOdoo: attach` and prove a breakpoint hit, local variables, and the selected
host/container mappings. Runtime startup belongs to downstream validation.

For indexing investigations, record extension version, workspace/source identity, and cold/warm conditions. Define the
XML/Python navigation or diagnostic check and measurement start before each run. Measure usable features separately from
scan completion; observed success times are upper bounds on readiness. Repeat the same checks after changes and include
failures/timeouts.
