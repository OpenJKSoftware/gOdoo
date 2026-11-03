# gOdoo repository contracts

## Ownership and routing

- The host owns Git, workspace commands, VS Code, linting, and the development `.venv`. Odoo uses `VIRTUAL_ENV`,
  otherwise the project `.venv`. Downstream owns Docker, system packages, mounts, and service orchestration.
- Use `godoo-source-workspaces` for source/workspace changes, `godoo-runtime-validation` for runtime or downstream
  adoption, and both only when crossing those boundaries. Keep shared Docker validation sequential.
- Use `godoo-editor-validation` for editor/debugger checks, `godoo-changeset-review` for broad reviews, and
  `godoo-cli-development` for Python, scripts, or CLI contracts.

## Branches and publication

- Start independent changes on short-lived `codex/<topic>` branches from freshly fetched `origin/main`; preserve
  existing work and use separate worktrees for concurrent changes.
- Before branch/worktree/PR or release operations, use `godoo-release-management` for ownership checks, branch
  lifecycle, Quality gates, and release procedures. Push and merge only within explicit authorization.

## Validation

- Implementers run focused checks for owned behavior. The named integration owner runs `make lint` and `make test`
  sequentially on the final implementation tree, plus applicable editor, workspace, runtime, and live-surface checks.
- Whole-changeset review follows `godoo-changeset-review`: independent first pass on the current staged, unstaged, and
  untracked snapshot, then targeted follow-up using implementer reports.
- Use editor-validation for affected typing/editor boundaries; report source checks, editor results, built artifacts,
  and live behavior separately.
