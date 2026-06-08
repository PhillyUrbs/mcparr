"""mcparr - an MCP gateway for the *arr ecosystem and Plex media server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcparr")
except PackageNotFoundError:  # pragma: no cover - package not installed
    __version__ = "0.0.0+unknown"
