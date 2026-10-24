"""plugboard's INI configuration."""

from configparser import ConfigParser


def _parser(text):
    parser = ConfigParser()
    parser.read_string(text)
    return parser


def read_config(text):
    """Return {section: {option: value}} for INI text.

    Values may refer to other options of their section, or of [DEFAULT], as
    %(name)s; the DEFAULT options appear in every section.
    """
    parser = _parser(text)
    return {section: dict(parser.items(section)) for section in parser.sections()}


def enabled_plugins(text):
    """Return the names of the [plugin:NAME] sections whose enabled option is true (the default)."""
    parser = _parser(text)
    return [section[len("plugin:"):] for section in parser.sections()
            if section.startswith("plugin:") and parser.getboolean(section, "enabled", fallback=True)]
