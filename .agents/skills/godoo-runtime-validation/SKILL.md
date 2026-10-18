---
name: godoo-runtime-validation
description:
  Validate gOdoo runtime lifecycle and upstream-change adoption in downstream Compose projects, including installed
  revision checks, init/launch, subprocess output, debugging, endpoints, and real Odoo integration tests.
---

# gOdoo runtime validation

Resolve the gOdoo checkout and downstream project first. Read `docs/lifecycle.md`, `docs/downstream.md`, and the
relevant implementation/tests in that gOdoo checkout. Read the downstream Make target, Dockerfile, and selected Compose
files for the behavior under investigation.

## Identify what is being tested

Record the upstream revision and relevant uncommitted changes, the downstream revision, and its configured gOdoo
dependency or pin. For an available built or running target, also establish the image ID and installed gOdoo revision
from build evidence or package metadata. A host checkout, Dockerfile pin, or package version alone does not prove which
commit is running. If the revision cannot be established, report that gap before drawing runtime conclusions.

Keep Odoo/addon source provenance separate from the installed gOdoo CLI revision. Resolve the selected Compose project,
services, source mounts, and database/filestore target; avoid dumping unrelated environment values.

## Check downstream adoption

Trace each relevant upstream contract change to its downstream consumer and focused validation: command flags,
environment options, source paths, lifecycle hooks, or generated proxy configuration. Keep source and CLI behavior
upstream; downstream owns images, Compose, mounts, runtime users, service ordering, and project hooks. Check whether the
downstream pin includes the required fix before proposing another implementation.

`runtime init` owns restore, bootstrap, and reconciliation as a one-shot job. `runtime launch` only starts prepared
state. Keep `init` ahead of `app` with `service_completed_successfully`. gOdoo provides no downstream service
orchestration.

Use the downstream Make/Compose path for authorized runtime validation. Reuse host-resolved workspace paths rather than
assembling a substitute container with guessed addon mounts. A pin change needs a rebuilt artifact and, when live
verification is in scope, a recreated target before it establishes runtime behavior. Temporary source overlays prove
only the overlaid configuration.

Start the downstream debug service separately before attaching with generated `gOdoo: attach`. Report source checks,
built-artifact checks, runtime startup, debugger attachment, lifecycle state, and endpoint results separately. Never
start services merely to validate source/editor configuration. A compatibility review does not itself authorize
rebuilds, resets, restores, or upgrades; use any existing authorization and resolve the target before stateful work.

## When progress or logging is the problem

Trace child stdout/stderr through gOdoo, the init service, and Compose. Check terminal and captured-output behavior
where relevant, including native progress streams, feedback during quiet finite phases, and process exit status. Input
reaching 100% does not establish that a restore finished. Captured-output tests do not prove terminal redraw; check the
reported Make/Compose surface when authorized and available, otherwise name that remaining verification.

Finish with the affected contracts, incompatibilities and their owning repository, the smallest required repair, and
checks tied to the revision/environment actually tested. Stop a review when those findings and remaining verification
steps are clear; perform repairs or runtime operations only within the requested scope.
