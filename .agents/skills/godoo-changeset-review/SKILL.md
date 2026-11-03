---
name: godoo-changeset-review
description: Review broad gOdoo changesets across CLI, workspace, and runtime contracts.
---

# gOdoo changeset review

The coordinator supplies a current diff and coverage manifest covering staged, unstaged, and relevant untracked files.
Complete the independent first pass before reading implementer reports or prior findings; use those only for targeted
follow-up.

For lifecycle changes, compare the existing owner, trigger, completion state, retry behavior, and downstream caller.
Check persisted effects across entrypoints; a smaller diff can still duplicate state or weaken recovery.

Separate demonstrated regressions from optional cleanup. Review alone authorizes no repairs, source synchronization, or
runtime replacement.
