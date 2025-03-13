"""Version information."""

__version__ = "0.14.4"  # Initial version, will be managed by Hatch


def get_version_tuple() -> tuple[int, int, int]:
    """Convert the version string into a tuple of integers."""
    major, minor, patch = map(int, __version__.split("."))
    return major, minor, patch
