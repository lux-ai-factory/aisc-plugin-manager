"""The calling process (the engine) never imports a plugin: loading a package, a plugin or refreshing one
is refused with a reason that points at the plugin's stored manifest, and no plugin module is left in
sys.modules."""
import sys

import pytest

from aisc_plugin_manager import loader as loader_module
from aisc_plugin_manager.loader import Loader
from conftest import make_plugin


def refusal():
    found = getattr(loader_module, "CodeNotImported", None)
    if found is None:
        pytest.fail("the loader has no refusal to import plugin code (CodeNotImported): it imports it")
    return found


@pytest.mark.parametrize("call", ["load_package", "load_plugin", "refresh_package"])
def test_loading_plugin_code_is_refused_in_this_process(plugin_root, no_registry, call):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0", cls="DemoPlugin")
    ld = Loader(str(plugin_root), "http://devpi.invalid", "root/public")
    version = next(iter(ld.list_packages(refresh=True)["aisc-plugin-demo"]))
    args = {"load_package": ("aisc-plugin-demo", version), "load_plugin": ("aisc-plugin-demo", "DemoPlugin", version),
            "refresh_package": ("aisc-plugin-demo", version)}[call]
    refused = refusal()
    with pytest.raises(refused, match="stored manifest"):
        getattr(ld, call)(*args)
    assert not [m for m in sys.modules if m.startswith("aisc_plugin_demo")]
