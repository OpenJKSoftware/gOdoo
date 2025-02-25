# 🚀 GitHub Workflows Documentation

This directory contains the GitHub Actions workflows for the gOdoo-cli project. Below are the flowcharts explaining the different processes.

## 📈 Version Management Process

```mermaid
flowchart TD
    A[Manual Trigger] -->|version-bump.yml| B[Create Release Branch]
    B --> C[Bump Version in __about__.py]
    C --> D[Create PR]
    D --> E[PR Triggers quality.yml]
    E --> F{Lint and Test}
    F -->|Pass| G{Docker Build}
    F -->|Fail| Z[Fix Issues]
    G -->|Pass| H[PR Review]
    G -->|Fail| Z[Fix Issues]
    H -->|Merged| I[version-publish.yml]
    I --> J[Create Git Tag]
    J --> K[Create GitHub Release]
    K --> L[Publish to PyPI]
```

## 🔍 Quality Check Process

```mermaid
flowchart TD
    A[PR Created/Updated] --> B[quality.yml]
    B --> C[Install Dependencies]
    C --> D{Lint}
    C --> E{Test with Coverage}
    D -->|Pass| F{Docker Build}
    D -->|Fail| G[Fail PR]
    E -->|Pass| F
    E -->|Fail| G
    F -->|Pass| H[PR Ready]
    F -->|Fail| G
```

## 🔄 Workflow Overview

```mermaid
flowchart LR
    A[PR Created] -->|Triggers| B[quality.yml]
    B -->|Blocks/Allows| C[PR Merge]
    C -->|Triggers| D[version-publish.yml]
    D -->|Creates Tag & Release| E[Publish to PyPI]
```

## 💾 Caching Strategy

To optimize the workflow execution time, we leverage caching for the following:

- 📦 **Python dependencies**: The `actions/setup-python` action is used with the `cache: "pip"` option to cache Python packages. The cache key is based on the `pyproject.toml` file.
- 🔧 **Pre-commit hooks**: The `.pre-commit-config.yaml` file is used as the cache key for pre-commit hooks.

This multi-level caching strategy ensures that workflows run efficiently while still maintaining cache freshness and relevance.

## 📋 Workflow Details

### 🔼 version-bump.yml
- **Trigger**: Manual workflow dispatch
- **Options**: patch, minor, major, alpha, beta, rc
- **Actions**:
  1. Creates release branch
  2. Updates version using Hatch
  3. Creates PR with version bump

### ✅ quality.yml
- **Trigger**: Pull request events, push to main, manual dispatch
- **Actions**:
  1. Runs linting suite
  2. Runs tests with coverage
  3. Builds Docker image
- **Status**: Required check for PR merge

### 📦 version-publish.yml
- **Trigger**: PR merged with 'release' label
- **Actions**:
  1. Creates Git tag
  2. Creates GitHub release
  3. Builds Python package
  4. Publishes to PyPI

## 📝 Notes
- ⚙️ Quality checks run automatically on PR creation/update
- 🔢 Version bumps are initiated manually
- 🚀 Release process is automated after PR merge
- 🔒 All steps have appropriate permissions and concurrency limits
