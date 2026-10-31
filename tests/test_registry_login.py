"""Registry login: the client logs in only when user and password are both non-empty. Empty strings mean a
public index, where POST /+login would answer 401 and break registry discovery."""
import httpx
import pytest

from aisc_plugin_manager.devpi_client import DevpiClient


def recording_devpi(client: DevpiClient) -> list[tuple[str, str]]:
    requests = []

    def answer(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/+login":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"result": {"projects": []}})
    client.client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(answer))
    return requests


@pytest.mark.parametrize("user, password, logins", [("", "", 0), ("u", "p", 1)])
def test_the_client_logs_in_only_with_both_credentials_non_empty(user, password, logins):
    client = DevpiClient("http://devpi.invalid", "root/public", user, password)
    requests = recording_devpi(client)
    client.list_versions()
    assert requests.count(("POST", "/+login")) == logins
    assert ("GET", "/root/public") in requests
