"""Registry plugins: every version in the index is loadable, not only the latest (2026-10-05: uploading
data-monitor 0.4.1 made the installed 0.4.0 "not found", and every configuration page of it failed)."""
import httpx

from aisc_plugin_manager import devpi_client
from aisc_plugin_manager.devpi_client import DevpiClient
from aisc_plugin_manager.loader import Loader

INDEX = {"aisc-plugin-langbite": ["0.2.4", "0.2.6", "0.2.5"], "data-monitor": ["0.4.0", "0.4.1"]}


def fake_devpi(client: DevpiClient):
    def answer(request):
        path = request.url.path.rstrip("/")
        if path == "/root/public":
            return httpx.Response(200, json={"result": {"projects": sorted(INDEX)}})
        name = path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"result": {v: {} for v in INDEX[name]}})
    client.client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(answer))
    return client


def test_the_client_lists_every_version_of_each_package():
    client = fake_devpi(DevpiClient("http://devpi.invalid", "root/public"))
    assert {k: sorted(v) for k, v in client.list_versions().items()} == \
        {"aisc-plugin-langbite": ["0.2.4", "0.2.5", "0.2.6"], "data-monitor": ["0.4.0", "0.4.1"]}


def test_the_catalogue_still_gets_the_latest_version_only():
    client = fake_devpi(DevpiClient("http://devpi.invalid", "root/public"))
    assert client.list_packages() == {"aisc-plugin-langbite": "0.2.6", "data-monitor": "0.4.1"}


def test_the_loader_knows_an_older_version_still_in_the_index(tmp_path):
    loader = Loader(str(tmp_path), "http://devpi.invalid", "root/public")
    fake_devpi(loader.devpi_client)
    packages = loader.list_packages(refresh=True)
    assert set(packages["data-monitor"]) == {"0.4.0", "0.4.1"}
    assert set(packages["aisc-plugin-langbite"]) == {"0.2.4", "0.2.5", "0.2.6"}
    assert packages["data-monitor"]["0.4.0"]["source"] == "registry"
