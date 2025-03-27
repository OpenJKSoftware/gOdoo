"""Version information."""

from packaging import version

__version__ = "0.14.9"  # Initial version, will be managed by Hatch


def get_version_tuple() -> tuple[int, int, int]:
    """Convert the version string into a tuple of integers."""
    vers = version.parse(__version__)
    return vers.major, vers.minor, vers.micro
