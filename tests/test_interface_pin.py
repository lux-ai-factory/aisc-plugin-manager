"""The package asks for aisc-plugin-interface 0.4.0, and its lock takes it from the wheel under vendor/,
never from PyPI (whose latest, 0.3.0, has no assesses, helpers or connections).

Run under the lock, so the last case sees the lock's environment:

    uv run --locked --with pytest python -m pytest -q -p no:cacheprovider tests/test_interface_pin.py
"""
import hashlib
import importlib
import importlib.metadata
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WHEEL = "aisc_plugin_interface-0.4.0-py3-none-any.whl"


def _name(requirement: str) -> str:
    return re.split(r"[\s<>=!~;\[(]", requirement, maxsplit=1)[0].strip().lower().replace("_", "-")


def test_the_interface_pin_is_0_4_0():
    deps = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["dependencies"]
    assert [d for d in deps if _name(d) == "aisc-plugin-interface"] == ["aisc-plugin-interface>=0.4.0"]


def test_the_lock_takes_the_interface_from_vendor():
    lock = tomllib.loads((REPO / "uv.lock").read_text())
    entries = [p for p in lock.get("package", []) if p.get("name") == "aisc-plugin-interface"]
    assert len(entries) == 1, entries
    (entry,) = entries
    assert entry.get("version") == "0.4.0"
    assert entry.get("source") == {"path": f"vendor/{WHEEL}"}
    wheel = REPO / "vendor" / WHEEL
    assert wheel.is_file(), f"vendor/{WHEEL} is missing"
    assert (entry.get("wheels") or [{}])[0].get("hash") == "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert not [e for e in entries if "registry" in e.get("source", {})]


def test_the_installed_interface_is_0_4_0():
    assert importlib.metadata.version("aisc-plugin-interface") == "0.4.0"
    importlib.import_module("aisc_plugin_interface.assesses")
