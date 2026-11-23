"""Adapter discovery in the plugin-manager (ADR 0009 phase 2)."""
from __future__ import annotations

import textwrap

from aisc_plugin_manager.loader import BUILTIN_PACKAGE, Loader

_PLUGIN = '''
from pydantic import BaseModel
from aisc_plugin_interface import BaseEvaluationPlugin
from aisc_plugin_interface.adapters import DataAdapter, EndpointAdapter, Preset, PresetField


class Config(BaseModel):
    pass


class DemoPlugin(BaseEvaluationPlugin[Config]):
    plugin_name = "Demo"

    def evaluate(self, config_data):
        return []


class DemoDataAdapter(DataAdapter):
    id = "demo-data"
    label = "Demo data"
    reads = ("csv",)
    produces = ("records",)

    def load(self, source, produce):
        return []


class DemoEndpointAdapter(EndpointAdapter):
    id = "demo-endpoint"
    label = "Demo endpoint"
    speaks = "rest"
    serves = ("openai-chat",)

    def build_request(self, target, secret, *, turn):
        raise NotImplementedError

    def parse_response(self, status, text, target):
        raise NotImplementedError


DEMO_PRESET = Preset(id="demo", label="Demo", kind="endpoint", protocol="rest", adapter="demo-endpoint",
                     fields=(PresetField("url", "URL", target="url", required=True),))
'''

_ADAPTER_ONLY = '''
from aisc_plugin_interface.adapters import DataAdapter


class OnlyAdapter(DataAdapter):
    id = "only"
    label = "Only"
    reads = ("*",)
    produces = ("file",)

    def load(self, source, produce):
        return source
'''

_BROKEN = '''
from aisc_plugin_interface.adapters import DataAdapter


class BrokenAdapter(DataAdapter):
    id = "broken"
    reads = ("csv",)
    produces = ("records",)

    @classmethod
    def config_schema(cls):
        raise RuntimeError("broken")

    def load(self, source, produce):
        return None
'''


def _write_package(root, dirname, package_name, module_name, body, version="1.0.0"):
    pkg = root / dirname
    (pkg / "src" / module_name).mkdir(parents=True)
    (pkg / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [project]
            name = "{package_name}"
            version = "{version}"
            dependencies = ["aisc-plugin-interface>=0.5.0"]
            """
        )
    )
    (pkg / "src" / module_name / "__init__.py").write_text(textwrap.dedent(body))
    return pkg


def _loader(root):
    loader = Loader(str(root), "", "")
    loader.devpi_client = None  # no registry in the test
    return loader


def test_local_package_adapters_and_presets_are_discovered(tmp_path):
    _write_package(tmp_path, "demo-pkg", "demo-pkg", "demo_pkg", _PLUGIN)
    loader = _loader(tmp_path)
    catalogue = loader.list_adapters(refresh=True)

    assert BUILTIN_PACKAGE in catalogue
    demo = catalogue["demo-pkg"]["1.0.0"]
    ids = {a["id"] for a in demo["adapters"]}
    assert ids == {"demo-data", "demo-endpoint"}
    assert {p["id"] for p in demo["presets"]} == {"demo"}

    data = next(a for a in demo["adapters"] if a["id"] == "demo-data")
    assert data["kind"] == "data" and data["reads"] == ["csv"] and data["produces"] == ["records"]
    endpoint = next(a for a in demo["adapters"] if a["id"] == "demo-endpoint")
    assert endpoint["kind"] == "endpoint" and endpoint["speaks"] == "rest" and endpoint["serves"] == ["openai-chat"]


def test_the_builtin_library_is_listed(tmp_path):
    loader = _loader(tmp_path)
    catalogue = loader.list_adapters(refresh=True)
    builtin = next(iter(catalogue[BUILTIN_PACKAGE].values()))
    ids = {a["id"] for a in builtin["adapters"]}
    assert {"csv", "tsv", "parquet", "openai", "rest", "a2a", "oip"} <= ids
    assert builtin["source"] == "builtin"
    assert {p["id"] for p in builtin["presets"]} == {"llm", "agent", "model-api", "dataset", "model-file"}


def test_an_adapter_only_package_is_not_a_plugin(tmp_path):
    _write_package(tmp_path, "adapter-only", "adapter-only", "adapter_only", _ADAPTER_ONLY)
    loader = _loader(tmp_path)
    loader.list_adapters(refresh=True)

    assert "adapter-only" not in loader.list_packages(kind="plugin")
    assert "adapter-only" in loader.list_adapters()


def test_a_broken_adapter_is_skipped_not_fatal(tmp_path):
    _write_package(tmp_path, "broken-pkg", "broken-pkg", "broken_pkg", _BROKEN)
    loader = _loader(tmp_path)
    catalogue = loader.list_adapters(refresh=True)
    assert catalogue["broken-pkg"]["1.0.0"]["adapters"] == []


def test_load_adapter_returns_the_class(tmp_path):
    _write_package(tmp_path, "demo-pkg", "demo-pkg", "demo_pkg", _PLUGIN)
    loader = _loader(tmp_path)
    loader.list_adapters(refresh=True)
    cls = loader.load_adapter("demo-pkg", "1.0.0", "demo-data")
    assert cls.id == "demo-data"


def test_load_builtin_adapter_does_not_install(tmp_path):
    loader = _loader(tmp_path)
    cls = loader.load_adapter(BUILTIN_PACKAGE, "0.5.0", "csv")
    assert cls.id == "csv"


def test_a_plugin_package_still_loads_plugins(tmp_path):
    _write_package(tmp_path, "demo-pkg", "demo-pkg", "demo_pkg", _PLUGIN)
    loader = _loader(tmp_path)
    plugins = loader.load_package("demo-pkg", "1.0.0")
    assert "DemoPlugin" in plugins
