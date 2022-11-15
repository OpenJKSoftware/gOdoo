import typer

from ...helpers.cli import typer_unpacker


@typer_unpacker
def rpc_callback(
    ctx: typer.Context,
    odoo_rpc_host: str = typer.Option(
        ...,
        envvar="ODOO_RPC_HOST",
        help="Odoo RPC Host",
    ),
    odoo_main_db: str = typer.Option(
        ...,
        envvar="ODOO_MAIN_DB",
        help="Odoo Database for RPC Calls",
    ),
    odoo_rpc_user: str = typer.Option(
        ...,
        envvar="ODOO_RPC_USER",
        help="User for RPC login",
    ),
    odoo_rpc_password: str = typer.Option(
        ...,
        envvar="ODOO_RPC_PASSWORD",
        help="Password RPC Login Password",
    ),
):
    ctx.obj.odoo_rpc_host = odoo_rpc_host
    ctx.obj.odoo_main_db = odoo_main_db
    ctx.obj.odoo_rpc_user = odoo_rpc_user
    ctx.obj.odoo_rpc_password = odoo_rpc_password
