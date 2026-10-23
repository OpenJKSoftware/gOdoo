# CI and releases

[Documentation](../../README.md#documentation) · [Repository architecture](../../docs/architecture.md)

CI checks the host package. Release workflows publish Python distributions to PyPI and create a GitHub Release; they do
not publish runtime images or host-adapter bundles.

## Workflow map

| Workflow                                   | Trigger                                            | Result                                                           |
| ------------------------------------------ | -------------------------------------------------- | ---------------------------------------------------------------- |
| [Quality](quality.yml)                     | Pull request, push to `main`, or manual dispatch   | Python 3.11/3.12 checks and test/coverage artifacts              |
| [Version bump](version-bump.yml)           | Manual dispatch with a bump type                   | Version change on `release/<version>` and an editable release PR |
| [Release and publish](version-publish.yml) | Merged PR into `main` carrying the `release` label | Verified Python package publication, tag, and GitHub Release     |

## Quality checks

Arrows show the steps in each Python job. The final artifact upload uses `if: always()`, so it is also attempted when an
earlier step fails; reports may be absent if their producing step never ran. The PR author diagnoses failures, and the
reviewer checks the linked Actions results before accepting the change.

```mermaid
flowchart TD
    Trigger["GitHub receives a pull request,<br/>a main push, or a manual Quality dispatch"]
    Trigger --> Matrix["GitHub Actions runs two independent jobs:<br/>Python 3.11 and Python 3.12"]
    Matrix --> Install["Each job: install locked uv dependencies"]
    Install --> Wheel["Build wheel and install it in a fresh environment;<br/>check package import and CLI help outside checkout"]
    Wheel --> Lint["make lint<br/>Check source rules and formatting"]
    Lint --> Test["make test<br/>Run repository tests and produce JUnit and coverage XML"]
    Test --> Upload["Upload test-results artifact<br/>for that Python version"]
    Install -.->|if a later step fails, still attempt upload| Upload
    Upload --> Review["PR author and reviewer inspect failures<br/>and test artifacts on the GitHub Actions run"]
```

The wheel smoke test installs into a fresh environment and checks imports and CLI help outside the checkout. The
workflow installs PostgreSQL development headers and copies `.env.sample` for repository tests. JUnit and coverage
upload is attempted even after failure. Real Odoo/PostgreSQL integration tests remain opt-in.

```bash
# Reproduce the main host checks locally after configuring the project .env.
uv sync --locked
make lint
make test

# Inspect recent CI runs with GitHub CLI.
gh run list --workflow quality.yml --limit 5
```

## Create a release

The maintainer chooses and reviews the release; GitHub Actions performs the version change and publication steps. The
release notes are the PR description. They are separate from the JUnit test and coverage artifacts above.

```mermaid
flowchart TD
    Maintainer["Maintainer manually runs<br/>Version Bump with a chosen bump type"]
    Maintainer --> Bump["version-bump.yml: update package version,<br/>push release/version branch, and open release PR"]
    Bump --> Review["Maintainer edits the PR's release notes<br/>and reviews its diff and available CI results"]
    Review --> Merge["Maintainer merges the release-labeled PR into main"]
    Merge --> Verify["version-publish.yml verifies release identity:<br/>repository, branch, PR title, version, merge commit, and tag"]
    Verify --> Publish["Build Python distributions<br/>and publish them to PyPI"]
    Publish --> Tag["Create vVERSION tag at the merge commit,<br/>or verify an existing tag points there"]
    Tag --> Release["Create GitHub Release using the merged PR description;<br/>preserve an existing release"]
```

```bash
# Maintainers: choose patch, minor, major, rc, beta, or alpha.
# This dispatch creates and pushes a release branch and opens a PR.
gh workflow run version-bump.yml --ref main -f bump_type=patch
```

Edit the generated PR description before merging: it becomes the release notes. Keep these identities aligned:

| Identity          | Required value                      |
| ----------------- | ----------------------------------- |
| Source repository | This repository                     |
| Target branch     | `main`                              |
| PR title          | `🔖 Release <version>`              |
| Source branch     | `release/<version>`                 |
| Package version   | The same version, verified by Hatch |
| Tag               | `v<version>` at the PR merge commit |

The publication workflow rejects an existing tag pointing elsewhere. It publishes to PyPI before creating the tag and
GitHub Release. Existing GitHub Releases keep their descriptions; alpha, beta, and release-candidate versions are marked
as prereleases.

If a run fails, inspect the failed step and existing PyPI/tag/release state before retrying that run. Do not move an
existing release tag to bypass a mismatch.
