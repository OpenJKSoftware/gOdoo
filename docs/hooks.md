# Lifecycle hooks and native migrations

## Scope

gOdoo has one initialization flow. A major upgrade restores an explicitly supplied `upgraded.zip`; an ordinary restore
uses the matching dump and filestore. Both then use Odoo's native module loader and the same gOdoo hooks. There is no
transition directory, version-pair selector, or implicit update/install module list.

Odoo owns versioned schema and data migration through addon `migrations/<version>/pre-*.py`, `post-*.py`, and
`end-*.py`, including external roots passed with `--upgrade-path`. Keep `--pre-upgrade-script` as a passthrough to
native `--pre-upgrade-scripts` for cursor-based work required before module loading. It requires an update selection and
does not execute for install-only work. Native migrations need an eligible installed-to-manifest version change; fresh
installs need their own install behavior. Do not replace these mechanisms with gOdoo hook completion records.

## Discovery and public interface

`runtime init --hooks-dir PATH` is repeatable and has the environment fallback `GODOO_HOOK_DIRS`. Relative directories
resolve from the project working directory. Directories retain supplied order; their immediate `*.py` children run in
filename order. The option accepts directories, not individual files. Deduplicate resolved directories. Do not recurse
or automatically scan project directories. Missing directories, malformed declarations, duplicate hook IDs, and unknown
phases/outcomes fail before lifecycle mutation.

Each file declares `HOOK = Hook(...)` and defines `run(ctx)`. An optional `needs_run(ctx) -> bool` is read-only. Import
`Hook` from `godoo_cli.runtime.hooks`. Files must be safe to import without Odoo: keep Odoo imports inside database
callbacks and do not perform work at module scope. Discovery loads definitions, validates them, and does not call
database predicates. `Hook` is a frozen value with these fields:

| Field          | Contract                                                                                                                         |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `id`           | Required, nonempty stable identifier, independent of filename.                                                                   |
| `phase`        | One of `prepare`, `after_bootstrap`, `after_restore`, `after_reconcile`.                                                         |
| `on`           | Tuple of applicable initialization outcomes: `ready`, `bootstrapped`, `restored`. Default is all.                                |
| `environments` | Optional tuple of exact `GODOO_ENVIRONMENT` values. Omitted means unrestricted; an allowlist never matches an unset environment. |

Every phase uses the same declaration, eligibility rules, `needs_run(ctx)`, `run(ctx)`, logging, and failure handling.
Database access is available only in database phases. Predicates must return an actual boolean; an exception is a
failure, never a skip. A missing predicate means run whenever eligible.

```python
from godoo_cli.runtime.hooks import Hook

HOOK = Hook(
    id="wetech.module_requests",
    phase="prepare",
)


def run(ctx):
    ctx.request_modules(update=["wetech_base"], install=["helpdesk"])
```

Preparation runs every init attempt, without starting Odoo, before module dependency preflight or database mutation. It
recomputes requests on retries. Its context exposes `environment` and `request_modules(update=..., install=...)`. Module
requests merge additively with explicit CLI/environment selections and deduplicate names in first-occurrence order. No
request means no implicit action; `all` must be requested explicitly. Native upgrade paths and pre-upgrade scripts
remain explicit CLI/environment inputs. Preparation cannot access `env`; it must not mutate the database or external
systems. Outcome filters are invalid for preparation because bootstrap/restore classification has not happened yet.

Database phases expose `ctx.env`, `ctx.environment`, and `ctx.outcome`. They cannot request modules: native
reconciliation has explicit inputs and must not be started recursively from a database hook. Environment is a deployment
input, never inferred from database contents. Hooks must not parse marker files.

```python
from godoo_cli.runtime.hooks import Hook

HOOK = Hook(
    id="wetech.neutralize",
    phase="after_reconcile",
    on=("restored",),
    environments=("development", "review", "staging"),
)


def needs_run(ctx):
    return not bool(ctx.env["ir.config_parameter"].sudo().get_param("database.is_neutralized"))


def run(ctx):
    from odoo.modules.neutralize import neutralize_database

    neutralize_database(ctx.env.cr)
```

## Execution and transaction boundaries

Filter phase, outcome, and environment before starting a database subprocess. If no eligible hooks remain, start no Odoo
shell. Otherwise start one shell for the entire phase, using the configured Odoo executable/interpreter, database
target, addon paths, and native upgrade paths. Load its registry once. Execute eligible hooks sequentially; do not run
database hooks concurrently.

Within that shell, open a fresh registry cursor and environment per hook. Evaluate `needs_run` and execute `run` in that
hook's transaction. Roll back a skipped predicate's transaction. Flush and commit only after successful action
completion; roll back uncommitted work on failure and stop. Propagate the subprocess failure to init. Fresh environments
prevent stale transaction state leaking across hooks. Hooks normally do not call `commit` or `rollback`; the runner owns
those actions.

Odoo may commit inside an invoked operation, and external effects cannot be rolled back by this runner. Such hooks still
require state guards and restart-safe actions. Native module updates run in the separate native reconciliation process,
outside the hook batch. Ordinary database hooks must not rebuild the registry or perform immediate module install/update
operations.

Do not run database predicates or after-restore ORM hooks before required native pre-upgrade scripts. Reject a selected
after-restore database hook combined with native pre-upgrade scripts before mutation. Put post-upgrade environment setup
in after-reconcile instead.

Log each hook ID and a decision: excluded by declaration, already satisfied, running, completed, or failed. Do not log
passwords or complete environment maps.

## Idempotence and recovery

The lifecycle marker records unfinished init and preserves its original outcome until every phase succeeds. A retry
after restore still exposes `restored` even though the database already exists. A later ordinary init exposes `ready`.
Existing marker/restore recovery and database-major guards remain in force.

Execution is at least once. There is no generic `once=True`, hook-success ledger, or file-hash cache. State predicates
must observe desired state: a prior success does not establish that configuration is still correct after edits or
restores.

- Compute desired values once in a helper shared by the predicate and action.
- Find managed records by stable identities; fail on ambiguous matches.
- Write only changed fields; repeated assignments can trigger tracking or hashing.
- Delete only remaining matches; an empty result is success.
- Validate record identity and schema preconditions before mutation.
- Let exceptions fail initialization. Do not hide partial schemas or corrupt data.
- Keep actions safe after a partial internal commit, even if the predicate ran.
- For external actions, provide an operation-specific idempotency mechanism.

Use native versioned migrations for one-time version changes. `on=("restored",)` means eligible during restore
initialization and its retries, not exactly once. An action's durable database guard, such as Odoo's neutralization
flag, prevents repeating completed work during those retries.

## Restore selection and Odoo_1 acceptance

`--dump-path DATASET` selects `odoo.dump` and the original filestore. `--dump-path DATASET --seed DATASET/upgraded.zip`
explicitly selects the upgraded archive and infers the paired original filestore. The existence of an old `upgraded.zip`
never changes ordinary dump selection. Preserve archive validation, attachment overlays, retry handling, and the rule
that seeds do not replace an already initialized database.

Odoo_1 owns conversion of its 16-to-19 cleanup and repairs into native pre-upgrade and addon migrations. Module-request
hooks replace text lists. Non-production neutralization, audit cleanup, SMTP routing, checksum policy, and local
provisioning remain explicit reconciliation responsibilities. Native pre-upgrade cleanup must be guarded by actual
module/data state so it also accepts an already upgraded 19 dump. Manifest versions must be checked against versions in
the upgraded fixture.

Prove the 16-to-19 upgraded archive and a subsequent ordinary 19 dump restore, including retry, repeat init without
extra writes, changed configuration, native migration selection, module requests, and environment policy. Isolate live
tests from production and existing project databases. Source tests and mocked batches do not establish successful
restoration of a production-derived archive.

## Required validation

Unit tests cover discovery/validation before mutation, stable ordering and IDs, declaration filtering without shell
startup, strict predicates, one registry load per phase, separate hook transactions, failure propagation, additive
module requests, preparation replay, preserved outcome on retry, native pre-upgrade ordering, and
plain-versus-explicit-upgraded dataset selection. Test predicate failure, action failure, and a partial internal commit
separately.

Final integration runs gOdoo lint and tests sequentially, relevant typing checks, and available isolated Odoo
integration. Report editor UI, archive-based upgrade, ordinary 19 restore, and installed-package adoption as separate
evidence.
