---
name: godoo-runtime-validation
description:
  Validate gOdoo runtime lifecycle in downstream Compose deployments, including init, runtime init/launch, debugging,
  endpoint availability, and real Odoo integration tests.
---

# gOdoo runtime validation

Read `docs/lifecycle.md`, `docs/downstream.md`, and relevant lifecycle implementation/tests before runtime work.

`runtime init` owns restore, bootstrap, and reconciliation as a one-shot job. `runtime launch` only starts prepared
state. Keep `init` ahead of `app` with `service_completed_successfully`. The downstream project owns Compose, service
startup, runtime users, and debug startup; gOdoo provides no downstream service orchestration.

Validate runtime through downstream Compose and Make commands only when authorized. Start the downstream debug service
separately before attaching with generated `gOdoo: attach`; report configuration, runtime startup, debugger attachment,
lifecycle state, and endpoint results separately. Never start services merely to validate source/editor configuration.
