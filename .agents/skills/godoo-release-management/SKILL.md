---
name: godoo-release-management
description:
  Prepare, review, merge, and verify gOdoo releases using the repository's version-bump and publication workflows.
---

# gOdoo release management

Read `.github/workflows/README.md` and inspect the current release state before acting. Publish or merge only when
explicitly authorized. Keep the release PR short-lived and coordinate other merges to preserve its intended scope.

Dispatch `.github/workflows/version-bump.yml` against `main` with the selected bump type. The workflow creates
`release/<version>` and its release PR; leave version changes to it and keep the PR limited to release preparation. Do
not use long-lived `release` or `develop` branches for this workflow.

The release PR description becomes the release notes. Never replace existing release text or remove its changelog
summary. If additions are needed, replace the placeholder and preserve the generated changelog and remaining text.
Review the notes, release-preparation diff, release label, source repository, target branch, title, branch name, package
version, and available Quality results against the identities in `.github/workflows/README.md` before merging.

After the authorized merge, verify publication to PyPI, the `v<version>` tag at the release merge commit, and the GitHub
Release. If publication fails, inspect the failed step and existing PyPI, tag, and release state before retrying. Never
move an existing release tag to repair publication. Report the released version, revision, validation, and any
downstream adoption required.
