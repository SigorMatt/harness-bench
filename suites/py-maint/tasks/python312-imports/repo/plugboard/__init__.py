"""A small plugin host: plugins are Python source files, enabled in an INI configuration."""

from ._version import __version__
from .compat import version_at_least
from .config import enabled_plugins, read_config
from .loader import load_source
from .registry import IncompatiblePlugin, Plugin, Registry, load_plugins

__all__ = ["IncompatiblePlugin", "Plugin", "Registry", "__version__", "enabled_plugins", "load_plugins",
           "load_source", "read_config", "version_at_least"]
