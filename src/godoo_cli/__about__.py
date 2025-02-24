"""Version information."""

from typing import Tuple

__version__ = "0.13.2"


def get_version_tuple() -> Tuple[int, int, int]:
    """Convert the version string into a tuple of integers."""
    major, minor, patch = map(int, __version__.split("."))
    return major, minor, patch
