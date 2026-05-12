# gOdoo Agent Notes (High-Signal Only)

Purpose: keep only repo-specific guidance that is easy for agents to miss.

## Scope

- This repo is a Python CLI (`godoo`) plus DevContainer tooling for Odoo work.
- Ignore generic Python/Typer/Odoo best practices unless they are enforced here.
- Prefer existing patterns over introducing new abstractions.

## Non-Obvious Rules

1. Logging contract is mandatory.

- Every Python module should define `LOGGER = logging.getLogger(__name__)`.
- Use `LOGGER.*` for operational logs.
- Do not use `print()` for operational output; user-facing terminal output can use `rich.print()`.
- Logging setup is centralized via `helpers.system.set_logging()`.

2. New CLI commands need 2 registration points.

- Add/Export command module in `src/godoo_cli/commands/__init__.py`.
- Wire it into the CLI app in `src/godoo_cli/cli.py`.

3. CLI options are env-first by design.

- Reuse option definitions from `src/godoo_cli/cli_common.py` where possible.
- New options should include env var fallback (do not create CLI-only knobs without reason).

4. Manifest handling has strict expectations.

- `odoo_manifest.yml` is the source of truth for source repos.
- The `odoo` section is required; missing/empty manifest is treated as an error.
- Preserve YAML comments/shape by using manifest helpers (roundtrip loader behavior), not ad-hoc yaml dumps.

5. Path and typing discipline matter.

- Prefer `pathlib.Path` over raw path strings.
- Keep type hints compatible with Python 3.9+.

6. Odoo test runs are intentionally single-threaded in command flows.

- Preserve worker/thread behavior in test command paths unless intentionally changing test semantics.

## Fast Navigation

- Main CLI entry and wiring: `src/godoo_cli/cli.py`
- Shared CLI options/env mapping: `src/godoo_cli/cli_common.py`
- Command packages: `src/godoo_cli/commands/`
- Manifest model/parsing: `src/godoo_cli/models/godoo_manifest.py`
- Logging setup/utilities: `src/godoo_cli/helpers/system.py`

## Validation Commands

- Lint: `hatch run dev:lint`
- Tests: `hatch run dev:test`
- CI-equivalent local run: `hatch run dev:ci`

## DevContainer/Runtime Notes

- Primary access path is Traefik (`*.docker.localhost`), not raw port assumptions.
- `make` targets are the canonical runtime flows (`make`, `make bare`, `make reset`, `make reset-hard`, `make kill`).

## Agent Editing Policy

- Keep edits minimal and local to the request.
- Do not rewrite working command surfaces unless asked.
- Prefer updating docs/help text only when behavior changes.

---

Last updated: 2026-05-12
