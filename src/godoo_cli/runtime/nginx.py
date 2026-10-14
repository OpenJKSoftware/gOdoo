"""Generate Nginx mappings for eligible Odoo addon static files."""

import ast
import logging
import os
import re
import tempfile
from pathlib import Path

from ..models import GodooConfig, OdooVersion

LOGGER = logging.getLogger(__name__)


def write_nginx_static_config(  # noqa: C901 - keep root precedence and shadowing together.
    config: GodooConfig, destination: Path, version: OdooVersion
) -> None:
    """Write a deterministic Nginx map for addon static directories."""
    roots = (
        config.odoo_install_folder / "odoo" / "addons",
        config.data_dir / "addons" / version.raw,
        *config.addon_paths,
        config.odoo_install_folder / "addons",
    )
    entries: dict[str, str] = {}
    seen_roots: set[Path] = set()
    manifest_names = ("__manifest__.py",) if version.major >= 19 else ("__manifest__.py", "__openerp__.py")

    for root in roots:
        if root in seen_roots or not root.is_dir():
            continue
        seen_roots.add(root)
        for addon in sorted(root.iterdir()):
            if not addon.is_dir() or not re.fullmatch(r"\w{1,256}", addon.name):
                continue
            if addon.name in entries:
                continue
            value = None
            for manifest_name in manifest_names:
                manifest = addon / manifest_name
                if not manifest.is_file():
                    continue
                try:
                    candidate = ast.literal_eval(manifest.read_text(encoding="utf-8"))
                except (OSError, SyntaxError, ValueError, TypeError):
                    LOGGER.debug("Skipping invalid addon manifest %s", manifest)
                    continue
                if isinstance(candidate, dict):
                    value = candidate
                    break
            if value is None:
                continue
            entries[addon.name] = ""
            static = addon / "static"
            if static.is_dir() and (value.get("installable", True) or value.get("assets")):
                entries[addon.name] = str(addon.parent.absolute())

    lines = ["map $odoo_static_module $odoo_static_root {", "    default /dev/null;"]
    for module, addon_path in sorted(entries.items()):
        if not addon_path:
            continue
        unsafe_path = any(character == "$" or ord(character) < 32 or ord(character) == 127 for character in addon_path)
        if unsafe_path:
            message = f"Nginx addon path contains unsafe characters: {addon_path!r}"
            raise ValueError(message)
        escaped_path = addon_path.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'    "~^{module}$" "{escaped_path}";')
    lines.append("}")
    content = "\n".join(lines) + "\n"

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fchmod(temporary.fileno(), 0o644)
        os.replace(temporary_path, destination)
        LOGGER.info("Wrote Nginx addon static map to %s", destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
