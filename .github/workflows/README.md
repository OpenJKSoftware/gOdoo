# Workflows

`quality.yml` runs on pull requests, pushes to `main`, and manual dispatch. Its Python 3.11 and 3.12 matrix installs dependencies, builds and smoke tests the wheel, runs `make lint` and `make test`, and uploads test and coverage reports. Odoo/PostgreSQL integration tests are opt-in and do not run in this workflow.

`version-bump.yml` creates a release PR on manual dispatch. Edit the release description in that PR before merging it. `version-publish.yml` then publishes the Python package to PyPI before creating the tag and GitHub Release. A failed publish run can be retried; it verifies any existing tag and reuses artifacts already on PyPI. No runtime image or host-adapter bundle is published.
