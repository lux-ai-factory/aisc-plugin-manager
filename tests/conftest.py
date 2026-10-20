import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PLUGIN_CODE = textwrap.dedent('''
    from pydantic import BaseModel
    from aisc_plugin_interface import BaseEvaluationPlugin

    class Config(BaseModel):
        x: int = 1

    class {cls}(BaseEvaluationPlugin[Config]):
        plugin_name = "{display}"

        def evaluate(self, config_data):
            return {{}}
''')


def make_plugin(root: Path, folder: str, name: str, version: str, *, cls="DemoPlugin",
                display="Demo", tool_aisc: str | None = None, depends=True, module_code=None):
    pkg = root / folder
    module = pkg / name.replace("-", "_")
    module.mkdir(parents=True)
    deps = '["aisc-plugin-interface>=0.2.0"]' if depends else '["requests"]'
    pyproject = f'[project]\nname = "{name}"\nversion = "{version}"\ndependencies = {deps}\n'
    if tool_aisc:
        pyproject += f"\n[tool.aisc]\n{tool_aisc}\n"
    (pkg / "pyproject.toml").write_text(pyproject)
    code = module_code if module_code is not None else PLUGIN_CODE.format(cls=cls, display=display)
    (module / "__init__.py").write_text(code)
    return pkg


@pytest.fixture
def plugin_root(tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    return root


@pytest.fixture
def no_registry(monkeypatch):
    from aisc_plugin_manager import devpi_client
    monkeypatch.setattr(devpi_client.DevpiClient, "list_packages", lambda self: {})
    monkeypatch.setattr(devpi_client.DevpiClient, "list_versions", lambda self: {})


@pytest.fixture(autouse=True)
def clean_modules():
    before = set(sys.modules)
    path_before = list(sys.path)
    yield
    for name in set(sys.modules) - before:
        if name.startswith(("demo", "aisc_plugin_demo", "aisc_plugin_langbite")):
            del sys.modules[name]
    sys.path[:] = path_before
