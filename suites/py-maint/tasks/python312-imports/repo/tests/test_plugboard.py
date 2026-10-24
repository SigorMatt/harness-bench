from plugboard import enabled_plugins, load_plugins, load_source, read_config, version_at_least

CONFIG = """
[plugin:greeter]

[plugin:shouter]
enabled = no

[plugin:future]
"""

SOURCES = {
    "greeter": "def on_greet(name):\n    return 'hello ' + name\n",
    "shouter": "def on_greet(name):\n    return name.upper()\n",
    "future": "REQUIRES = '9.0'\n\ndef on_greet(name):\n    return name\n",
}


def test_read_config_interpolates():
    config = read_config("[db]\nhost = localhost\nurl = postgres://%(host)s/app\n")
    assert config["db"]["url"] == "postgres://localhost/app"


def test_enabled_plugins():
    assert enabled_plugins(CONFIG) == ["greeter", "future"]


def test_versions_compare_as_numbers():
    assert version_at_least("2.10", "2.9")
    assert not version_at_least("2.3.1", "2.10")


def test_load_source_runs_the_code():
    assert load_source("p", "X = 6 * 7\n").X == 42


def test_plugins_needing_a_newer_host_are_left_out():
    registry = load_plugins(CONFIG, SOURCES, host_version="2.3.1")
    assert registry.names() == ["greeter"]
    assert registry.call("on_greet", "ada") == {"greeter": "hello ada"}
