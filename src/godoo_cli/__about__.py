"""Version information."""

from typing import Tuple

__version__ = "0.14.3"  # Initial version, will be managed by Hatch


def get_version_tuple() -> Tuple[int, int, int]:
    """Convert the version string into a tuple of integers."""
    major, minor, patch = map(int, __version__.split("."))
    return major, minor, patch
