import importlib
import inspect
import sys
import logging
import tomllib
from pathlib import Path
from typing import Dict, Type

from .devpi_client import DevpiClient
from aisc_plugin_interface.base_evaluation_plugin import BaseEvaluationPlugin
from .uv_client import uv_install

logger = logging.getLogger(__name__)
DEFAULT_PLUGIN_PATH = "plugins"


def find_module_directory(pkg_root: Path) -> Path | None:
    """
    Strictly checks for:
    1. pkg_root/module_name/__init__.py
    2. pkg_root/src/module_name/__init__.py
    """
    # Case 2: src/module structure
    src_dir = pkg_root / "src"
    if src_dir.exists() and src_dir.is_dir():
        for subdirectory in src_dir.iterdir():
            if subdirectory.is_dir() and (subdirectory / "__init__.py").exists():
                return subdirectory

    # Case 1: Direct module structure
    for subdirectory in pkg_root.iterdir():
        if subdirectory.is_dir() and (subdirectory / "__init__.py").exists():
            return subdirectory

    return None


class Loader:
    def __init__(self, local_plugin_path: str, registry_url: str, registry_index: str, registry_user: str,
                 registry_password: str):
        self.plugin_dirs = [Path(local_plugin_path), Path(DEFAULT_PLUGIN_PATH)]
        self.devpi_client = DevpiClient(registry_url, registry_index, registry_user, registry_password)

        self.discovered_packages: Dict[str, Dict[str, dict]] = {}
        self._loaded_plugins: Dict[str, BaseEvaluationPlugin] = {}

    def _discover_local_packages(self):
        for plugin_dir in self.plugin_dirs:
            if not plugin_dir.exists() or not plugin_dir.is_dir():
                continue
            for pkg_root in plugin_dir.iterdir():
                if not pkg_root.is_dir():
                    continue
                module_path = find_module_directory(pkg_root)
                if module_path:
                    pyproject_path = pkg_root / "pyproject.toml"
                    if pyproject_path.exists():
                        try:
                            with open(pyproject_path, "rb") as f:
                                toml_data = tomllib.load(f)
                                
                                package_name = toml_data.get("project", {}).get("name")
                                version = toml_data.get("project", {}).get("version")

                                if not package_name or not version:
                                    logger.warning(f"Missing name or version in pyproject.toml for {pkg_root.name}")
                                    continue

                                if package_name not in self.discovered_packages:
                                    self.discovered_packages[package_name] = {}

                                self.discovered_packages[package_name][version] = {
                                    "source": "local",
                                    "pkg_root": pkg_root,
                                    "module_name": module_path.name,
                                    "import_path": str(module_path.parent.resolve())
                                }
                        except Exception as e:
                            logger.warning(f"Failed to read pyproject.toml for {pkg_root.name}: {e}")

    def _discover_registry_packages(self):
        if not self.devpi_client:
            return
        try:
            registry_packages = self.devpi_client.list_packages()
            for package_name, versions in registry_packages.items():
                if package_name not in self.discovered_packages:
                    self.discovered_packages[package_name] = {}

                if isinstance(versions, str):
                    versions = [versions]

                for version in versions:
                    if version not in self.discovered_packages[package_name]:
                        self.discovered_packages[package_name][version] = {
                            "source": "registry",
                            "package": package_name,
                            "module_name": package_name.replace("-", "_"),
                        }
        except Exception as e:
            logger.error(f"Failed to list registry plugins: {e}")

    def list_packages(self, refresh: bool = False) -> Dict[str, Dict[str, dict]]:
        """Returns the nested dictionary of available packages and their versions."""
        if refresh or not self.discovered_packages:
            self.discovered_packages.clear()
            self._discover_local_packages()
            self._discover_registry_packages()
        return self.discovered_packages

    def _extract_plugin_classes(self, module) -> Dict[str, Type[BaseEvaluationPlugin]]:
        found_plugins = {}
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseEvaluationPlugin) and obj is not BaseEvaluationPlugin:
                plugin_key = obj.__name__
                found_plugins[plugin_key] = obj
        return found_plugins

    def load_package(self, package_name: str, version: str) -> Dict[str, BaseEvaluationPlugin]:
        """Installs/Imports a package, caches, and returns all found plugin instances."""
        if not self.discovered_packages:
            self.list_packages()

        if package_name not in self.discovered_packages:
            raise KeyError(f"Package '{package_name}' not found.")

        available_versions = self.discovered_packages[package_name]

        if version not in available_versions:
            raise KeyError(f"Version '{version}' of package '{package_name}' not found.")

        package_meta = available_versions[version]
        module_name = package_meta["module_name"]

        # Clear existing module from sys.modules to ensure fresh load
        if module_name in sys.modules:
            modules_to_remove = [m for m in sys.modules if m == module_name or m.startswith(f"{module_name}.")]
            for m in modules_to_remove:
                del sys.modules[m]

        if package_meta["source"] == "local":
            import_path = package_meta["import_path"]
            if import_path not in sys.path:
                sys.path.insert(0, import_path)
        elif package_meta["source"] == "registry":
            target = f'{package_name}=={version}'
            uv_install(target, extra_index_url=self.devpi_client.simple_index_url, no_deps=True)

        sys.path_importer_cache.clear()
        importlib.invalidate_caches()

        module = importlib.import_module(module_name)
        plugin_classes = self._extract_plugin_classes(module)

        if not plugin_classes:
            raise ValueError(f"No plugins found in package '{package_name}' (v{version})")

        # Instantiate and cache all plugins found in the package
        instances = {}
        for name, cls in plugin_classes.items():
            instance = cls()
            instances[name] = instance
            cache_key = f"{package_name}::{version}::{name}"
            self._loaded_plugins[cache_key] = instance

        return instances

    def load_plugin(self, package_name: str, plugin_name: str, version: str) -> BaseEvaluationPlugin:
        """Helper to extract a specific plugin directly from its package, favoring the cache."""
        if not self.discovered_packages:
            self.list_packages()

        cache_key = f"{package_name}::{version}::{plugin_name}"

        if cache_key in self._loaded_plugins:
            return self._loaded_plugins[cache_key]

        plugins = self.load_package(package_name, version)
        if plugin_name not in plugins:
            raise KeyError(f"Plugin '{plugin_name}' not found in package '{package_name}'")

        return plugins[plugin_name]