import logging
from base64 import b64decode
from pathlib import Path

import typer
from godoo_rpc.login import wait_for_odoo

from ...cli_common import CommonCLI
from ...helpers.modules import godooModule, godooModules
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
    godoo_modules: list[godooModule],
    upgrade_modules: bool = True,
):
    """Dump translations of given Modules into their addon folders.

    Parameters
    ----------
    modules : rpc modules
        Api Models of modules
    godoo_modules : list[godooModule]
        godoo_module instances of modules
    upgrade_modules : bool, optional
        Wether to upgrade modules before dumping, by default True
    """

    if upgrade_modules:
        LOGGER.info("Upgrading Modules: '%s'", ", ".join(modules.mapped("name")))
        modules.button_immediate_upgrade()

    for mod in modules:
        godoo_mod = [m for m in godoo_modules if m.name == mod.name]
        if not godoo_mod:
            raise ValueError(f"Module {mod.name} not found in godoo_modules")
        godoo_mod = godoo_mod[0]
        pot_path: Path = godoo_mod.path / "i18n" / (mod.name + ".pot")
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

    addons = godooModules(workspace_folder).get_modules()
    for addon in addons:
        if not incomplete or addon.name.startswith(incomplete):
            yield addon.name


@CLI.arg_annotator
def dump_translations(
    modules: list[str] = typer.Argument(
        ...,
        help="Module Name(s) space Seperated. Only works in workspace addon path",
        autocompletion=complete_workspace_addon_names,
    ),
    workspace_addon_path=CLI.odoo_paths.workspace_addon_path,
    rpc_host=CLI.rpc.rpc_host,
    rpc_database=CLI.rpc.rpc_db_name,
    rpc_user=CLI.rpc.rpc_user,
    rpc_password=CLI.rpc.rpc_password,
    upgrade_modules: bool = typer.Option(True, help="Upgrade modules before exporting"),
):
    """Dump Translations of module to <module_folder>/i18n/<module_name>.pot"""
    godoo_modules = list(godooModules(workspace_addon_path).get_modules(modules))
    module_names = [m.name for m in godoo_modules]
    LOGGER.debug("Found modules: %s", module_names)

    odoo_api = wait_for_odoo(
        odoo_host=rpc_host,
        odoo_db=rpc_database,
        odoo_user=rpc_user,
        odoo_password=rpc_password,
    )

    rpc_modules = rpc_get_modules(odoo_api, modules, module_names)
    if not rpc_modules:
        LOGGER.warning("No installed Modules found for Query: '%s'", modules)
        return CLI.returner(1)

    _dump_translations(
        modules=rpc_modules,
        godoo_modules=godoo_modules,
        upgrade_modules=upgrade_modules,
    )
