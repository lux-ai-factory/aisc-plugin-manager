"""A local package declares its default trustworthiness dimensions in its own [tool.aisc] table."""
from aisc_plugin_manager.loader import Loader
from conftest import make_plugin


def listed(plugin_root):
    loader = Loader(str(plugin_root), "http://devpi.invalid", "root/public")
    return loader.list_packages(refresh=True)["aisc-plugin-demo"]["0.1.0+local"]


def test_a_local_package_declares_its_dimensions(plugin_root, no_registry):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0", tool_aisc='dimensions = ["REQ5"]')
    assert listed(plugin_root)["dimensions"] == ["REQ5"]


def test_unknown_declared_dimensions_are_ignored(plugin_root, no_registry):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0", tool_aisc='dimensions = ["REQ99", "fairness", "REQ2"]')
    assert listed(plugin_root)["dimensions"] == ["REQ2"]


def test_a_package_without_declared_dimensions_has_none(plugin_root, no_registry):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0")
    assert listed(plugin_root)["dimensions"] == []


def test_a_malformed_dimensions_value_is_ignored(plugin_root, no_registry):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0", tool_aisc='dimensions = "REQ5"')
    assert listed(plugin_root)["dimensions"] == []
