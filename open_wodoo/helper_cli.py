import functools
import inspect
import logging
import os
import sys
from typing import Callable

import typer
from typer.models import ParameterInfo

from .helper import ensure_dotenv

LOGGER = logging.getLogger(__name__)


def typer_retuner(ret):
    """Exit Typer command with return code, if parent of calling function is not the typer_unpacker wrapper.
        Will just return ret if ret is either not an integer, or the parent function was another python func.

        Ensures, that we exit the CLI with a return code, only if the function is the CLI base command.

    Parameters
    ----------
    ret : Any
        Value to either return or Exit

    Returns
    -------
    Any
        ret
    """
    parent_func = sys._getframe(1).f_code.co_name
    if isinstance(ret, int):
        parent_name = sys._getframe(2).f_code.co_name
        if parent_name != "typer_unwrapper":
            LOGGER.debug("Exiting Typer Command: %s with code %s", parent_func, ret)
            if ret > 255:
                LOGGER.debug("Capping exit code to 255")
                ret = 255
            raise typer.Exit(code=ret)
    LOGGER.debug("Returning Typer Command: %s", parent_func)
    return ret


def typer_unpacker(f: Callable):
    """
    Apply as decorator to typer command function to make the function callable from python
    if you used typer.Argument or typer.Option.

    """

    @functools.wraps(f)
    def typer_unwrapper(*args, **kwargs):
        # Get the default function argument that aren't passed in kwargs via the
        # inspect module: https://stackoverflow.com/a/12627202
        missing_default_values = {
            k: (v.default, v._annotation)
            for k, v in inspect.signature(f).parameters.items()
            if v.default is not inspect.Parameter.empty and k not in kwargs
        }

        for name, (func_default, target_type) in missing_default_values.items():
            # If the default value is a typer.Option or typer.Argument, we have to
            # pull either the .default attribute and pass it in the function
            # invocation, or call it first.
            if isinstance(func_default, ParameterInfo):
                if ev := func_default.envvar:
                    env_var = os.getenv(ev)
                    try:
                        if env_res := target_type(env_var) if target_type else env_var:
                            kwargs[name] = env_res
                            continue
                    except TypeError as e:
                        raise TypeError(
                            f"Type mismatch in: '{ev}' Expected: '{target_type}' Got: '{type(env_var)}"
                        ) from e
                if callable(func_default.default):
                    kwargs[name] = func_default.default()
                else:
                    if func_default.default is Ellipsis:
                        raise ValueError(f"Missing required Argument for: {name}")
                    kwargs[name] = func_default.default

        # Call the wrapped function with the defaults injected if not specified.
        return f(*args, **kwargs)

    return typer_unwrapper


@typer_unpacker
def rpc_callback(
    ctx: typer.Context,
    odoo_rpc_host: str = typer.Option(..., envvar="ODOO_RPC_HOST", help="Odoo RPC Host"),
    odoo_main_db: str = typer.Option(..., envvar="ODOO_MAIN_DB", help="Odoo Database for RPC Calls"),
    odoo_rpc_user: str = typer.Option(..., envvar="ODOO_RPC_USER", help=""),
    odoo_rpc_password: str = typer.Option(
        ..., envvar="ODOO_RPC_PASSWORD", help="Password for admin user on Bootstrap. and RPC Login Password"
    ),
):
    ctx.obj.odoo_rpc_host = odoo_rpc_host
    ctx.obj.odoo_main_db = odoo_main_db
    ctx.obj.odoo_rpc_user = odoo_rpc_user
    ctx.obj.odoo_rpc_password = odoo_rpc_password
