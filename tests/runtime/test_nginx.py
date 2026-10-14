"""Tests for generated addon static mappings."""

from pathlib import Path

import pytest

from godoo_cli.models.godoo_models import GodooConfig, OdooVersion
from godoo_cli.runtime.nginx import write_nginx_static_config


def _config(
    tmp_path: Path,
    *,
    data_dir: Path | None = None,
    addon_paths: tuple[Path, ...] | None = None,
) -> GodooConfig:
    install = tmp_path / "odoo"
    workspace = tmp_path / "workspace"
    thirdparty = tmp_path / "thirdparty"
    install.mkdir()
    workspace.mkdir()
    thirdparty.mkdir()
    return GodooConfig(
        odoo_install_folder=install,
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=workspace,
        thirdparty_addon_path=thirdparty,
        data_dir=data_dir or tmp_path / "data",
        resolved_addon_paths=addon_paths or (workspace, thirdparty),
    )


def _addon(root: Path, name: str, manifest: str = "{}", *, static: bool = True) -> Path:
    addon = root / name
    addon.mkdir(parents=True)
    (addon / "__manifest__.py").write_text(manifest, encoding="utf-8")
    if static:
        (addon / "static").mkdir()
    return addon


def test_maps_first_matching_addon_across_effective_roots(tmp_path: Path) -> None:
    config = _config(tmp_path)
    builtin = config.odoo_install_folder / "odoo" / "addons"
    data = config.data_dir / "addons" / "19.0"
    community = config.addon_paths[0]
    thirdparty = config.addon_paths[1]
    archive = config.odoo_install_folder / "addons"
    roots = [builtin, data, community, thirdparty, archive]
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
    _addon(builtin, "shared")
    _addon(data, "shared")
    _addon(community, "community")
    _addon(thirdparty, "vendor")
    _addon(archive, "archived")

    destination = tmp_path / "generated" / "static-map.conf"
    write_nginx_static_config(config, destination, OdooVersion("19.0", 19, 0))

    contents = destination.read_text(encoding="utf-8")
    assert contents.index('"~^archived$"') < contents.index('"~^community$"')
    assert contents.index('"~^community$"') < contents.index('"~^shared$"')
    assert contents.index('"~^shared$"') < contents.index('"~^vendor$"')
    assert f'"~^shared$" "{builtin.absolute()}";' in contents
    assert str(data / "shared") not in contents
    assert contents.rstrip().endswith("}")


def test_first_valid_manifest_shadows_even_without_static_or_installable(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    builtin = config.odoo_install_folder / "odoo" / "addons"
    later = config.addon_paths[0]
    builtin.mkdir(parents=True, exist_ok=True)
    _addon(builtin, "disabled", "{'installable': False}")
    later.mkdir(parents=True, exist_ok=True)
    _addon(later, "disabled")
    _addon(builtin, "no_static", "{}", static=False)
    _addon(later, "no_static")

    destination = tmp_path / "map.conf"
    write_nginx_static_config(config, destination, OdooVersion("19.0", 19, 0))

    assert '"disabled"' not in destination.read_text(encoding="utf-8")
    assert '"no_static"' not in destination.read_text(encoding="utf-8")


def test_legacy_manifest_and_assets_exception(tmp_path: Path) -> None:
    config = _config(tmp_path)
    root = config.odoo_install_folder / "odoo" / "addons"
    _addon(root, "legacy", "{'installable': False, 'assets': {'web.assets': []}}")
    (root / "legacy" / "__manifest__.py").unlink()
    (root / "legacy" / "__openerp__.py").write_text(
        "{'installable': False, 'assets': {'web.assets': []}}", encoding="utf-8"
    )
    destination = tmp_path / "map.conf"

    write_nginx_static_config(config, destination, OdooVersion("18.0", 18, 0))

    contents = destination.read_text(encoding="utf-8")
    assert f'"~^legacy$" "{root.absolute()}";' in contents
    assert contents.rstrip().endswith("}")


def test_odoo_18_prefers_modern_manifest(tmp_path: Path) -> None:
    config = _config(tmp_path)
    root = config.odoo_install_folder / "odoo" / "addons"
    root.mkdir(parents=True)
    addon = _addon(root, "modern", "{'installable': True}")
    (addon / "__openerp__.py").write_text("{'installable': False}", encoding="utf-8")
    destination = tmp_path / "map.conf"

    write_nginx_static_config(config, destination, OdooVersion("18.0", 18, 0))

    assert f'"~^modern$" "{root.absolute()}";' in destination.read_text(encoding="utf-8")


def test_module_map_keys_are_case_sensitive(tmp_path: Path) -> None:
    config = _config(tmp_path)
    root = config.odoo_install_folder / "odoo" / "addons"
    root.mkdir(parents=True)
    _addon(root, "module")
    uppercase_root = config.data_dir / "addons" / "19.0"
    uppercase_root.mkdir(parents=True)
    _addon(uppercase_root, "MODULE")
    destination = tmp_path / "map.conf"

    write_nginx_static_config(config, destination, OdooVersion("19.0", 19, 0))

    contents = destination.read_text(encoding="utf-8")
    assert f'"~^module$" "{root.absolute()}";' in contents
    assert f'"~^MODULE$" "{uppercase_root.absolute()}";' in contents
    assert '"~^Module$"' not in contents


@pytest.mark.parametrize("unsafe", ["$value", "line\nbreak", "control\x01"])
def test_rejects_unsafe_mapped_paths(tmp_path: Path, unsafe: str) -> None:
    unsafe_root = tmp_path / unsafe
    config = _config(tmp_path, addon_paths=(unsafe_root,))
    unsafe_root.mkdir()
    _addon(unsafe_root, "unsafe")
    destination = tmp_path / "map.conf"

    with pytest.raises(ValueError, match="unsafe characters"):
        write_nginx_static_config(config, destination, OdooVersion("19.0", 19, 0))


def test_quotes_and_backslashes_are_escaped_and_replacement_is_atomic(
    tmp_path: Path,
) -> None:
    spaced = tmp_path / 'addon "source"'
    config = _config(tmp_path, addon_paths=(spaced,))
    spaced.mkdir()
    _addon(spaced, "quoted")
    destination = tmp_path / "map.conf"
    destination.write_text("previous", encoding="utf-8")

    write_nginx_static_config(config, destination, OdooVersion("19.0", 19, 0))

    escaped_root = str(spaced.absolute()).replace('"', '\\"')
    assert f'"~^quoted$" "{escaped_root}";' in destination.read_text(encoding="utf-8")
    assert destination.stat().st_mode & 0o777 == 0o644
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        'addon "source"',
        "map.conf",
        "odoo",
        "thirdparty",
        "workspace",
    ]
