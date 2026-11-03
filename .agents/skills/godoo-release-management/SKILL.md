---
name: godoo-release-management
description: Handle gOdoo branch lifecycle, release preparation, and publication checks.
---

# gOdoo branches and releases

Use `.github/workflows/README.md` for workflow commands and release identity checks. Review alone authorizes no
dispatch, merge, or publication.

Topic-branch pushes alone do not run Quality; inspect PR Quality before merging. Do not start new work on a merged
branch. Check ancestry and worktree ownership before removing branches.

For an authorized version bump, dispatch `version-bump.yml` against `main`; let the workflow change the version and
create its release PR. Keep that PR limited to release preparation.

The PR body becomes the release notes. Preserve existing release text and generated changelog summaries; replace only
the summary placeholder when adding content.

After the authorized release merge, verify PyPI publication, `v<version>` at the merge commit, and the GitHub Release.
Inspect existing publication/tag/release state before retrying. Never move an existing release tag. Report downstream
adoption separately.
