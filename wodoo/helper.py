"""Provides Various Helper Functions for Odoo"""

import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from typing import List

import requests
from wodoo_rpc import OdooApiWrapper, login_odoo

LOGGER = logging.getLogger(__name__)


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
        logging.basicConfig(
            stream=sys.stdout,
            format="{asctime} - [{levelname}] - {message}",
            style="{",
            level=logging.DEBUG,
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        logging.basicConfig(
            stream=sys.stdout,
            format="{asctime} - [{levelname}] - {message}",
            style="{",
            level=logging.INFO,
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
