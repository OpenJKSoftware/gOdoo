import logging
from base64 import b64decode
from pathlib import Path

import typer
from godoo_rpc.login import wait_for_odoo

from ...helpers.cli import typer_retuner
from ...helpers.odoo_files import get_odoo_module_paths
from .cli import rpc_callback
from .modules import rpc_get_modules

app = typer.Typer(callback=rpc_callback, no_args_is_help=True)
LOGGER = logging.getLogger(__name__)


def dump_translation(module, target_path: Path):
    """Dump Translation of a module into POT file.

    Parameters
    ----------
    module : _type_
        _description_
    target_path : Path
        _description_
    """
    trans_exp_mod = module.env["base.language.export"]

    LOGGER.info("Exporting: %s --> %s", module.name, str(target_path))
    trans_wiz_id = trans_exp_mod.create({"format": "po", "modules": [module.id]})
    trans_wiz = trans_exp_mod.browse([trans_wiz_id])
    trans_wiz.act_getfile()
    trans_wiz = trans_exp_mod.browse([trans_wiz_id])
    target_path.parent.mkdir(exist_ok=True)
    target_path.write_bytes(b64decode(trans_wiz.data))


def _dump_translations(
    modules,
    workspace_addon_path: Path,
    upgrade_modules: bool = True,
):
    """Dump translations of given Modules into their addon folders.

    Parameters
    ----------
    odoo_api : OdooApiWrapper
        Odoo Api Wrapper
    modules : _type_
        Api Models of modules
    workspace_addon_path : Path
        path where those modules are installed
    upgrade_modules : bool, optional
        Wether to upgrade modules before dumping, by default True
    """

    if upgrade_modules:
        LOGGER.info("Upgrading Modules: '%s'", ", ".join(modules.mapped("name")))
        modules.button_immediate_upgrade()

    for mod in modules:
        ex_path: Path = workspace_addon_path / mod.name / "i18n"
        pot_path: Path = ex_path / (mod.name + ".pot")
        dump_translation(mod, pot_path)


def dump_translations(
    ctx: typer.Context,
    module_query=typer.Argument(..., help="Module Name. Add % to force =ilike match. Only valid for Workspace Addons."),
    upgrade_modules: bool = typer.Option(True, help="Upgrade modules before exporting"),
):

    addon_path = ctx.obj.workspace_addon_path
    addon_folders = get_odoo_module_paths(addon_path)
    valid_module_names = [str(p.stem) for p in addon_folders]
    LOGGER.debug("Found modules:\n%s", "\n".join(["\t" + mn for mn in valid_module_names]))

    odoo_api = wait_for_odoo(
        odoo_host=ctx.obj.odoo_rpc_host,
        odoo_db=ctx.obj.odoo_main_db,
        odoo_user=ctx.obj.odoo_rpc_user,
        odoo_password=ctx.obj.odoo_rpc_password,
    )

    modules = rpc_get_modules(odoo_api, module_query, valid_module_names)
    if not modules:
        LOGGER.warning("No installed Modules found for Query string: '%s'", module_query)
        return typer_retuner(1)

    _dump_translations(
        modules=modules,
        workspace_addon_path=addon_path,
        upgrade_modules=upgrade_modules,
    )
