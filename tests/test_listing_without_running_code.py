"""The index says which wheel of a version it holds by its sha256, read from the simple page; and a local
plugin folder is listed from its files alone (a module folder with __init__.py and a dependency on the plugin
interface), without running any of its code in the calling process."""
import httpx

from aisc_plugin_manager.devpi_client import DevpiClient
from aisc_plugin_manager.loader import Loader
from conftest import make_plugin

A, B = "a" * 64, "b" * 64
SIMPLE = f"""<!DOCTYPE html><html><body>
<a href="../../+f/1a2/demo_pkg-1.0-py3-none-any.whl#sha256={A}">demo_pkg-1.0-py3-none-any.whl</a>
<a href="../../+f/3b4/demo_pkg-1.0.tar.gz#sha256={'c' * 64}">demo_pkg-1.0.tar.gz</a>
<a href="../../+f/5c6/demo_pkg-1.1-py3-none-any.whl#sha256={B}">demo_pkg-1.1-py3-none-any.whl</a>
</body></html>"""


def simple_index(client: DevpiClient):
    asked = []

    def answer(request):
        asked.append(request.url.path)
        if request.url.path.rstrip("/") == "/root/public/+simple/demo-pkg":
            return httpx.Response(200, text=SIMPLE, headers={"Content-Type": "text/html"})
        return httpx.Response(404)
    client.client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(answer))
    return asked


def test_the_wheel_hash_comes_from_the_simple_page():
    client = DevpiClient("http://devpi.invalid", "root/public")
    simple_index(client)
    read = getattr(client, "wheel_sha256", None)
    assert read is not None, "the index client cannot say which wheel it holds"
    assert read("demo-pkg", "1.0") == A
    assert read("Demo_Pkg", "1.1") == B
    assert read("demo-pkg", "9.9") is None


def test_a_local_plugin_is_listed_without_running_its_code(plugin_root, no_registry, tmp_path):
    sentinel = tmp_path / "imported"
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0",
                module_code=f"open({str(sentinel)!r}, 'w').close()\n"
                            "from aisc_plugin_interface import BaseEvaluationPlugin\n"
                            "class DemoPlugin(BaseEvaluationPlugin):\n"
                            "    def evaluate(self, config_data):\n"
                            "        return {}\n")
    listed = Loader(str(plugin_root), "http://devpi.invalid", "root/public").list_packages(refresh=True)
    assert "aisc-plugin-demo" in listed
    assert not sentinel.exists(), "listing the local plugins ran the plugin's code"


def test_a_folder_without_its_module_or_the_interface_dependency_is_skipped(plugin_root, no_registry):
    make_plugin(plugin_root, "nodep", "aisc-plugin-nodep", "0.1.0", depends=False)
    moved = make_plugin(plugin_root, "moved", "aisc-plugin-moved", "0.1.0")
    (moved / "aisc_plugin_moved").rename(moved / "something_else")
    noinit = make_plugin(plugin_root, "noinit", "aisc-plugin-noinit", "0.1.0")
    (noinit / "aisc_plugin_noinit" / "__init__.py").unlink()
    assert Loader(str(plugin_root), "http://devpi.invalid", "root/public").list_packages(refresh=True) == {}
