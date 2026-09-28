"""Validate manifest selection constraints before workspace resolution."""

from pathlib import Path

import pytest

from godoo_cli.models import GodooManifest, ManifestError


def test_duplicate_thirdparty_repository_name_in_prefix_is_rejected(tmp_path: Path) -> None:
    """Different origins with one derived name cannot share a prefix."""
    manifest = tmp_path / "odoo_manifest.yml"
    manifest.write_text(
        """\
odoo: {url: "https://github.com/odoo/odoo.git"}
thirdparty:
  OCA:
    - {url: "https://github.com/OCA/server-tools.git"}
    - {url: "https://git.example.test/custom/server-tools.git"}
""",
        encoding="utf-8",
    )

    with pytest.raises(
        ManifestError,
        match="Duplicate third-party repository name 'server-tools' in prefix 'OCA'",
    ):
        GodooManifest.from_yaml_file(manifest)


def test_same_thirdparty_repository_name_in_distinct_prefixes_is_allowed(
    tmp_path: Path,
) -> None:
    """Prefix remains part of the third-party source identity."""
    manifest = tmp_path / "odoo_manifest.yml"
    manifest.write_text(
        """\
odoo: {url: "https://github.com/odoo/odoo.git"}
thirdparty:
  OCA:
    - {url: "https://github.com/OCA/server-tools.git"}
  Vendor:
    - {url: "https://git.example.test/custom/server-tools.git"}
""",
        encoding="utf-8",
    )

    parsed = GodooManifest.from_yaml_file(manifest)

    assert [repository.name for repository in parsed.thirdparty["OCA"]] == ["server-tools"]
    assert [repository.name for repository in parsed.thirdparty["Vendor"]] == ["server-tools"]
