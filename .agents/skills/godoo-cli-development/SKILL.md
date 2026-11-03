---
name: godoo-cli-development
description: Preserve gOdoo CLI option, output, and lifecycle contracts during development.
---

# gOdoo CLI contracts

New options need an environment fallback unless there is a concrete reason to omit one; reuse shared definitions in
`src/godoo_cli/commands/common.py`.

Use module logging for operational messages and reserve `rich.print()` for user-facing output. Avoid unused loggers in
passive metadata, value types, and re-exports.

Before adding lifecycle/configuration state, compare the existing owner, trigger, completion state, retry behavior, and
downstream caller. Extend the existing mechanism when it provides the required contract.
