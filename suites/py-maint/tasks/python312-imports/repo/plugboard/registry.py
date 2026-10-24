"""The plugins a host has loaded, and calling their hooks."""

from dataclasses import dataclass

from ._version import __version__
from .compat import version_at_least
from .config import enabled_plugins
from .loader import load_source


class IncompatiblePlugin(Exception):
    """A plugin that needs a newer host than this one."""

    def __init__(self, name, requires):
        super().__init__(f"plugin {name} needs host version {requires} or later")
        self.name = name
        self.requires = requires


@dataclass(frozen=True)
class Plugin:
    name: str
    requires: str  # the oldest host version it runs on
    hooks: tuple   # the names of its on_* functions, sorted


class Registry:
    def __init__(self, host_version=__version__):
        self.host_version = host_version
        self._loaded = {}

    def register(self, name, source):
        """Load a plugin from its source code. It sets REQUIRES to the oldest host version it runs on."""
        module = load_source(name, source)
        requires = str(getattr(module, "REQUIRES", "0"))
        if not version_at_least(self.host_version, requires):
            raise IncompatiblePlugin(name, requires)
        hooks = tuple(sorted(attr for attr in dir(module)
                             if attr.startswith("on_") and callable(getattr(module, attr))))
        plugin = Plugin(name, requires, hooks)
        self._loaded[name] = (plugin, module)
        return plugin

    def names(self):
        return sorted(self._loaded)

    def call(self, hook, *args):
        """Call hook in every plugin that has it, in name order; return {plugin name: result}."""
        return {name: getattr(module, hook)(*args)
                for name, (plugin, module) in sorted(self._loaded.items()) if hook in plugin.hooks}


def load_plugins(config_text, sources, host_version=__version__):
    """Return a Registry of the plugins config_text enables, loaded from sources ({name: source code}).

    Plugins that need a newer host are left out.
    """
    registry = Registry(host_version)
    for name in enabled_plugins(config_text):
        try:
            registry.register(name, sources[name])
        except IncompatiblePlugin:
            continue
    return registry
