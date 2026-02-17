"""Script used by gOdoo to pregenerate asset bundles in the Odoo filestore.

The script tries several internal APIs to be tolerant across multiple
Odoo versions (16+). It prints which method succeeded or prints
diagnostic information if all attempts fail.
"""

import contextlib

from odoo import api

env: api.Environment = env  # Just to silence pyright # pyright: ignore # NOQA

try:
    env["ir.qweb"]._pregenerate_assets_bundles()
    print("pregenerated: ir.qweb._pregenerate_assets_bundles")
except Exception:
    try:
        env["ir.qweb"]._pregenerate_assets()
        print("pregenerated: ir.qweb._pregenerate_assets")
    except Exception:
        try:
            env["ir.asset"]._pregenerate_bundles()
            print("pregenerated: ir.asset._pregenerate_bundles")
        except Exception:
            print("pregenerate failed; exceptions:")
            import traceback

            with contextlib.suppress(Exception):
                traceback.print_exc()

# Commit any DB changes
with contextlib.suppress(Exception):
    env.cr.commit()
