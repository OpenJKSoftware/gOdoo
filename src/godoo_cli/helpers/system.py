"""Helper functions around the host system"""
import logging
import os
import subprocess
from pathlib import Path

import click
import requests
from rich.logging import RichHandler
from rich.traceback import install as install_rich_traceback

from . import cli as godoo_cli_helpers

LOGGER = logging.getLogger(__name__)


def run_cmd(command: str, **kwargs) -> subprocess.CompletedProcess:
    """Runs command via subprocess.run

    Parameters
    ----------
    command : str
        Command string
    **kwargs
        get passed down to Run

    Returns
    -------
    CompletedProcess
    """
    LOGGER.debug("Launching Commandline: '%s'", command)
    if not kwargs.get("shell"):
        kwargs["shell"] = True
    proc = subprocess.run(command, **kwargs)
    return proc


def ensure_dotenv(varname: str) -> str:
    """
    Load Env Var.
    Raise Error if not Set.

    Parameters
    ----------
    varname : str
        env var name

    Returns
    -------
    str
        env value

    Raises
    ------
    ReferenceError
        if env var is none
    """
    var = os.getenv(varname)
    if var is None:
        raise ReferenceError(f"Env Variable: {varname} is not set")
    return var


def set_logging(verbose: bool = False) -> None:
    """
    Set the Logging Config according to passed arguments.

    Parameters
    ----------
    verbose : bool
        Wether to Log debug Messages
    """
    if verbose:
        install_rich_traceback(suppress=[click, godoo_cli_helpers])
        logging.basicConfig(
            level=logging.DEBUG,
            format="{message}",
            style="{",
            handlers=[
                RichHandler(
                    level=logging.DEBUG,
                    show_path=True,
                    rich_tracebacks=True,
                    tracebacks_show_locals=True,
                )
            ],
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="{message}",
            style="{",
            handlers=[RichHandler(level=logging.INFO, show_path=False, rich_tracebacks=False)],
            datefmt="%Y-%m-%d %H:%M:%S",
        )


def download_file(url: str, save_path: Path, chunk_size: int = 128) -> None:
    """Download file from URL.

    Parameters
    ----------
    url : _type_
        url to get file from
    save_path : _type_
        Where to save the file
    chunk_size : int, optional
        Chunk size to iterate over request, by default 128
    """
    LOGGER.debug("Downloading File: '%s' to '%s'", url, save_path)
    r = requests.get(url, stream=True)
    with open(save_path, "wb") as fd:
        for chunk in r.iter_content(chunk_size=chunk_size):
            fd.write(chunk)
