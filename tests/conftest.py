"""Shared test isolation for repository environment defaults."""

import pytest


@pytest.fixture(autouse=True)
def isolate_workspace_source_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require tests that resolve shared sources to opt into that environment."""
    monkeypatch.setenv("GODOO_SOURCES_ROOT", "")
    monkeypatch.delenv("ODOO_MANIFEST", raising=False)
