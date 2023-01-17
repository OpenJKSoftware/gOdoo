import logging
from base64 import b64decode
from pathlib import Path

import typer
from godoo_rpc.login import wait_for_odoo

from ...cli_common import CommonCLI
from ...helpers.odoo_files import get_odoo_module_paths
from .modules import rpc_get_modules

CLI = CommonCLI()
LOGGER = logging.getLogger(__name__)


def _dump_translation_for_module(module, target_path: Path):
    """Dump Translation of a module into POT file.

    Parameters
    ----------
    module : _type_
        rpc record of module to export
    target_path : Path
        target pot path
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
        _dump_translation_for_module(mod, pot_path)


def complete_workspace_addon_names(ctx: typer.Context, incomplete: str):
    """Autocomplete handler that searches modules in Workspace_addon_path

    Parameters
    ----------
    ctx : typer.Context
        Contains calling parameters
    incomplete : str
        Incomplete current entry

    Yields
    ------
    str
        folder name
    """
    workspace_folder = ctx.params.get("workspace_addon_path")
    if not workspace_folder:
        return
    workspace_folder = Path(workspace_folder)
    addon_folders = get_odoo_module_paths(workspace_folder)
    for fold in addon_folders:
        if not incomplete or fold.name.startswith(incomplete):
            yield fold.name


@CLI.arg_annotator
def dump_translations(
    module_query=typer.Argument(
        ...,
        help="Module Name(s) comma Seperated. Add % to force =ilike match. Only works in workspace addon path",
        autocompletion=complete_workspace_addon_names,
    ),
    workspace_addon_path=CLI.odoo_paths.workspace_addon_path,
    rpc_host=CLI.rpc.rpc_host,
    rpc_database=CLI.rpc.rpc_db_name,
    rpc_user=CLI.rpc.rpc_user,
    rpc_password=CLI.rpc.rpc_password,
    no_upgrade_modules: bool = typer.Option(
        False, "--no-upgrade-modules", help="don't upgrade modules before exporting"
    ),
):
    """Dump Translations of module to <module_folder>/i18n/<module_name>.pot"""
    addon_path = workspace_addon_path
    addon_folders = get_odoo_module_paths(addon_path)
    valid_module_names = [str(p.stem) for p in addon_folders]
    LOGGER.debug("Found modules:\n%s", "\n".join(["\t" + mn for mn in valid_module_names]))

    odoo_api = wait_for_odoo(
        odoo_host=rpc_host,
        odoo_db=rpc_database,
        odoo_user=rpc_user,
        odoo_password=rpc_password,
    )

    modules = rpc_get_modules(odoo_api, module_query, valid_module_names)
    if not modules:
        LOGGER.warning("No installed Modules found for Query string: '%s'", module_query)
        return CLI.returner(1)

    _dump_translations(
        modules=modules,
        workspace_addon_path=addon_path,
        upgrade_modules=not no_upgrade_modules,
    )
