"""Shared test isolation for repository environment defaults."""

import pytest


@pytest.fixture(autouse=True)
def isolate_workspace_source_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require tests to opt into host sources and banner settings."""
    monkeypatch.setenv("GODOO_SOURCES_ROOT", "")
    monkeypatch.delenv("ODOO_MANIFEST", raising=False)
    monkeypatch.setenv("ODOO_BANNER_TEXT", "")
    monkeypatch.setenv("ODOO_BANNER_BG_COLOR", "")
