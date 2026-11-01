# gOdoo repository contracts

Keep only repo-specific behavior that code and tooling do not make obvious. Prefer existing patterns and the smallest
coherent change.

## Execution

- The host owns Git, workspace commands, VS Code, linting, and the development `.venv`. Odoo runs in the selected
  environment (`VIRTUAL_ENV`, otherwise the project `.venv`). Downstream owns Docker and system packages; gOdoo never
  detects or orchestrates containers.
- Load the matching skill under `.agents/skills/` for source-workspace or runtime work; load both only when a change
  crosses those boundaries.
- Use `godoo-editor-validation` for VS Code diagnostics and development-flow checks, and `godoo-changeset-review` for
  whole-changeset reviews.
- Keep shared Docker validation sequential.

## Branches and releases

- Start each independent change on a short-lived `codex/<topic>` branch from freshly fetched `origin/main`. Do not reuse
  a merged branch for new work. Keep implementation, tests, and documentation for one behavior together.
- Before switching or creating a branch, inspect staged, unstaged, and untracked work. Preserve existing changes and
  verify the resulting diff against `origin/main`; use a separate Git worktree for concurrent independent work.
- Open topic PRs into `main` and check Quality results before merging. Topic-branch pushes alone do not trigger Quality.
  Follow the existing merge strategy and commit conventions. Push and merge only within explicit authorization.
- After merge, return to an updated `main` or start the next topic branch from current `origin/main`. Prune stale remote
  references and remove merged branches only after checking ancestry and worktree ownership; preserve unfinished work.
- Leave package version changes to `.github/workflows/version-bump.yml`, dispatched against `main` with the chosen bump
  type. It creates `release/<version>` and the release PR; keep that PR limited to preparing the release.
- Review the release PR description and available Quality results before merging: its description becomes the release
  notes. The release-labeled PR must target `main` and match the title, branch, and version identities documented in
  `.github/workflows/README.md`. Publication uses the release merge commit for PyPI, the tag, and the GitHub Release.
- Keep release PRs short-lived and coordinate other merges to preserve the intended release scope. Do not use a
  long-lived `release` or `develop` branch for this workflow, or move an existing release tag to repair publication.

## Code and CLI

- Every shell script under `scripts/` has a purpose comment immediately below its shebang.
- Operational Python modules define and use `LOGGER = logging.getLogger(__name__)`; passive metadata, value-type, and
  re-export modules do not carry unused loggers. Reserve `rich.print()` for user-facing output.
- Export command groups through `src/godoo_cli/commands/__init__.py` and attach them in
  `src/godoo_cli/commands/root.py`. Reuse env-first option metadata from `src/godoo_cli/commands/common.py`; new options
  need an environment fallback unless there is a concrete reason not to provide one.
- For Typer commands, keep behavior in the function docstring and parameter help in `Annotated` metadata.

## Validation

- Implementers run only focused proof for owned behavior; they never run full `make lint` or `make test` unless
  explicitly the integration owner.
- The coordinator or named integration owner runs `make lint` and `make test` sequentially on the final tree, plus
  applicable editor, workspace, runtime, and live-surface checks.
- For whole-changeset review, the validator receives a coordinator-generated current staged, unstaged, and untracked
  diff snapshot and coverage manifest before implementer reports or prior findings; it performs an independent first
  pass, then may consume those reports for a targeted second pass.
- Validate relevant Python typing boundaries and editor configuration with the editor-validation skill; lint and unit
  tests do not establish that editor diagnostics are clear.
