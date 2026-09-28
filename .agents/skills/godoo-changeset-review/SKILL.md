---
name: godoo-changeset-review
description:
  Review a broad gOdoo working-tree changeset for regressions, compatibility, and simplification across CLI, workspace,
  and runtime boundaries. Use for whole-changeset reviews, not routine single-file fixes.
---

# gOdoo changeset review

Establish the review baseline from staged, unstaged, and untracked files. Distinguish existing user work from edits made
during the review; do not stage or revert unrelated files.

Partition substantial reviews by contracts: source ownership and generated configuration, runtime/database behavior, and
CLI/public interfaces. Load only the matching source-workspace or runtime skill for each partition. Give workers file
ownership, the baseline, and the questions they must resolve; the parent owns shared validation and integration.
Delegate review slots from the parent so nested reviewers cannot exhaust capacity.

Trace changed behavior through its callers, command registration, environment options, tests, and documentation. Include
new untracked modules. Check host/container boundaries and retained compatibility paths where the diff crosses them. A
smaller file is not evidence of a simpler design; examine duplicated state, lock ownership, and failure recovery.

Return actionable findings with file locations, a concrete trigger, and the affected contract. Separate demonstrated
regressions from optional cleanup. Review alone does not authorize implementation, source synchronization, or runtime
replacement.

For authorized fixes, give the existing worker its in-scope failures to repair. Run focused checks during implementation
and `make lint` / `make test` after integration. Add editor or live-runtime checks only when the changed behavior needs
them. Report skipped or blocked validation separately from passing checks, and inspect worker evidence and the final
diff without repeating the entire investigation.
