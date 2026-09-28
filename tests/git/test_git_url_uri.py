"""Tests parsing and validation of Git SSH manifest URLs."""

import logging

import pytest

from godoo_cli.git.git_url import GitUrl
from godoo_cli.models import GodooGitRepo

LOGGER = logging.getLogger(__name__)


@pytest.mark.parametrize(
    ("url", "user", "port"),
    [
        ("ssh://git@github.com/OCA/server-tools.git", "git", None),
        ("ssh://git@github.com:2222/OCA/server-tools.git", "git", 2222),
        ("ssh://github.com/OCA/server-tools.git", "", None),
        ("[git@gitlab.wetech.local:222]:wetech/odoo-enterprise.git", "git", 222),
    ],
)
def test_ssh_uri_supports_manifest_names_and_compare_urls(url: str, user: str, port: int | None) -> None:
    """Guards the contract that ssh uri supports manifest names and compare urls."""
    parsed = GitUrl(url)

    expected_name = "odoo-enterprise" if url.startswith("[") else "server-tools"
    assert GodooGitRepo(url=url).name == expected_name
    assert parsed.user == user
    assert parsed.port == port
    expected_path = "wetech/odoo-enterprise" if url.startswith("[") else "OCA/server-tools"
    assert parsed.path == expected_path
    if url.startswith("["):
        assert parsed.transport == "ssh://git@gitlab.wetech.local:222/wetech/odoo-enterprise.git"
        assert parsed.canonical == GitUrl(parsed.transport).canonical


@pytest.mark.parametrize("url", ["ssh://host", "ssh://git@host/repo?token=secret", "ssh://git:secret@host/repo"])
def test_ssh_uri_rejects_missing_paths_and_embedded_secrets(url: str) -> None:
    """Guards the contract that ssh uri rejects missing paths and embedded secrets."""
    with pytest.raises(ValueError, match="Repository URLs") as error:
        GitUrl(url)

    assert "secret" not in str(error.value)
