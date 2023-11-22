"""Helper functions around the host system"""
import datetime
import logging
import os
import subprocess
from pathlib import Path
from typing import List, Union

import click
import requests
from rich.console import Console
from rich.logging import RichHandler
from rich.prompt import Confirm
from rich.table import Table
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
    LOGGER.debug("Running shell:\n%s", command)
    if not kwargs.get("shell"):
        kwargs["shell"] = True
    proc = subprocess.run(command, **kwargs)
    LOGGER.debug("Return Code: %s", proc.returncode)
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


def file_or_folder_size_mb(path: Path) -> float:
    """Get size of file or all files in folder summed in MB"""

    def file_size_mb(file: Path) -> float:
        """Get size of file in MB"""
        return file.stat().st_size / (1024 * 1024)

    if path.is_file():
        return file_size_mb(path)
    else:
        return sum([file_size_mb(f) for f in path.rglob("*")])


def path_has_content(path: Path):
    """If Path exists and is no empty dir"""
    if path.is_dir():
        return bool(path.glob("*"))
    else:
        return path.exists() and path.stat().st_size


def typer_ask_overwrite_path(paths: Union[List[Path], Path]) -> bool:
    """Checks if the provided Paths do already exist.
    Ignores 0 size files and empty folders.
    Prints table of Paths with size and Changedate.
    Prompts user to continue or abort typer.

    Returns
    ---------
    False, when we shall not overwrite files.
    True, when there are no files to override or we should override
    """

    if isinstance(paths, Path):
        paths = [paths]

    existing_paths = [p for p in paths if path_has_content(p)]
    if not existing_paths:
        return True
    table = Table()
    table.add_column("Name")
    table.add_column("Size", justify="right")
    table.add_column("Date Changed")

    for p in existing_paths:
        timestamp = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        path_size = round(file_or_folder_size_mb(p), 2)
        table.add_row(p.name, f"{path_size}mb", timestamp.strftime("%Y-%m-%d: %H:%M:%S"))
    LOGGER.warning("Found Existing Odoo Files:")
    Console().print(table)
    override = Confirm.ask("override?")
    if override:
        return True
    LOGGER.info("Aborting")
    return False
