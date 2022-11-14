"""
Import read_path into Odoo.
Will wait until Odoo is Online.
Places Systemparam to Track if import was already done.
"""

import logging
import re
from base64 import b64decode
from pathlib import Path
from typing import List

import typer
from wodoo_rpc import OdooApiWrapper, import_folder
from wodoo_rpc.login import wait_for_odoo

from .helper_cli import rpc_callback
from .helper_odoo_files import get_odoo_addons_in_folder

_app = typer.Typer(callback=rpc_callback)
LOGGER = logging.getLogger(__name__)


def rpc_disable_tours(odoo_api: OdooApiWrapper):
    company = odoo_api.session.env["res.company"]
    company.action_close_sale_quotation_onboarding()


def rpc_get_modules(
    odoo_api: OdooApiWrapper, module_query: str, installed: bool = True, valid_module_names: List[str] = None
):
    mod = odoo_api.session.env["ir.module.module"]
    mod.update_list()

    base_domain = ["&", ("state", "=", "installed" if installed else "uninstalled")]
    if "," in module_query:
        search_domain = [("name", "in", module_query.split(","))]
    else:
        if "%" in module_query:
            search_domain = [("name", "=ilike", module_query)]
        else:
            search_domain = [("name", "=", module_query)]

    if valid_module_names:
        base_domain.insert(1, "&")
        base_domain.append(("name", "in", valid_module_names))

    module_ids = mod.search(base_domain + search_domain)
    if module_ids:
        return mod.browse(module_ids)


@_app.command("import_folder", help="Imports all files in a Folder according to a regex")
def wodoo_import_folder(
    ctx: typer.Context,
    read_path: Path = typer.Option(
        ...,
        dir_okay=True,
        file_okay=False,
        readable=True,
        exists=True,
        help="Folder in which to search for import",
    ),
    file_regex: str = typer.Option(
        r"(?P<module>.*)\.(csv|py|xlsx|json)$",
        help="Regex for filesearch. Add group 'module' to set a Module for RPC import",
    ),
    image_regex: str = typer.Option(
        r"(?P<default_code>\d{6})\.(jpeg|png|jpg)$",
        help="Regex to search for Product images. Add Fields as regex group for Matching.",
    ),
    check_data_timestamp: bool = typer.Option(
        True,
        help="If true, Odoo remembers the Name of an uploaded File in a Serverparameter. Subsequent Imports will ignore the file if it hasnt changed.",
    ),
    skip_existing_ids: bool = typer.Option(False, help="Will skip import of already existing External IDs."),
):
    """
    Import Csv Files into Odoo.
    Adds an ir.config_parameter for each imported file,
    containing the modification time of the file.
    Will Skip already matching Timestamps and only import files that are new.
    """
    odoo_api = wait_for_odoo(
        odoo_host=ctx.obj.odoo_rpc_host,
        odoo_db=ctx.obj.odoo_main_db,
        odoo_user=ctx.obj.odoo_rpc_user,
        odoo_password=ctx.obj.odoo_rpc_password,
    )

    import_folder(
        odoo_api=odoo_api,
        read_path=read_path.absolute(),
        data_regex=re.compile(file_regex),
        image_regex=re.compile(image_regex),
        check_dataset_timestamp=check_data_timestamp,
        skip_existing_ids=skip_existing_ids,
    )

    rpc_disable_tours(odoo_api)


@_app.command(help="Upgrades or Installs Modules in Odoo via RPC.")
def upgrade_modules(
    ctx: typer.Context,
    module_name_query: str = typer.Argument(..., help=r"Module Internal name. Will use ilike Match if \% is present"),
    install: bool = typer.Option(True, help="Install Module if not already installed"),
):
    """Upgrades or Installs Modules in Odoo via RPC."""

    odoo_api = wait_for_odoo(
        odoo_host=ctx.obj.odoo_rpc_host,
        odoo_db=ctx.obj.odoo_main_db,
        odoo_user=ctx.obj.odoo_rpc_user,
        odoo_password=ctx.obj.odoo_rpc_password,
    )
    mod = odoo_api.session.env["ir.module.module"]
    mod.update_list()
    upgrade_modules = rpc_get_modules(odoo_api, module_name_query, installed=True)

    if install:
        install_modules = rpc_get_modules(odoo_api, module_name_query, installed=False)
        if install_modules:
            LOGGER.info("Installing Module: " + ", ".join(install_modules.mapped("name")))
            install_modules.button_immediate_install()

    if upgrade_modules:
        LOGGER.info("Updating Module: " + ", ".join(upgrade_modules.mapped("name")))
        upgrade_modules.button_immediate_upgrade()


@_app.command(help="Upgrades Addons and Exports Translation .pot file")
def dump_translations(
    ctx: typer.Context,
    module_query=typer.Argument(..., help="Module Name. Add % to force =ilike match. Only valid for Workspace Addons."),
    upgrade_modules: bool = typer.Option(True, help="Upgrade modules before exporting"),
):

    addon_path = ctx.obj.workspace_addon_path
    addon_folders = get_odoo_addons_in_folder(addon_path)
    valid_module_names = [str(p.stem) for p in addon_folders]
    LOGGER.debug("Found modules:\n%s", "\n".join(["\t" + mn for mn in valid_module_names]))

    odoo_api = wait_for_odoo(
        odoo_host=ctx.obj.odoo_rpc_host,
        odoo_db=ctx.obj.odoo_main_db,
        odoo_user=ctx.obj.odoo_rpc_user,
        odoo_password=ctx.obj.odoo_rpc_password,
    )
    trans_exp_mod = odoo_api.session.env["base.language.export"]
    modules = rpc_get_modules(odoo_api, module_query, valid_module_names)

    if not modules:
        LOGGER.warning("No installed Modules found for Query string: '%s'", module_query)
        return
    if upgrade_modules:
        LOGGER.info("Upgrading Modules: '%s'", ", ".join(modules.mapped("name")))
        modules.button_immediate_upgrade()

    for mod in modules:
        ex_path: Path = addon_path / mod.name / "i18n"
        pot_path: Path = ex_path / (mod.name + ".pot")
        LOGGER.info("Exporting: %s --> %s", mod.name, str(pot_path))
        trans_wiz_id = trans_exp_mod.create({"format": "po", "modules": [mod.id]})
        trans_wiz = trans_exp_mod.browse([trans_wiz_id])
        trans_wiz.act_getfile()
        trans_wiz = trans_exp_mod.browse([trans_wiz_id])
        ex_path.mkdir(exist_ok=True)
        pot_path.write_bytes(b64decode(trans_wiz.data))
