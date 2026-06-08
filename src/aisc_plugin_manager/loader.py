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


def get_expected_module_directory(pkg_root: Path, package_name: str) -> Path | None:
    """
    Enforces strict convention: looks for a module folder named
    exactly after the normalized package name inside src/ or the root.
    """
    module_name = package_name.replace("-", "_")

    # Check case: src/module_name/
    src_dir = pkg_root / "src" / module_name
    if src_dir.exists() and src_dir.is_dir():
        return src_dir

    # Check case: module_name/ (at root)
    root_dir = pkg_root / module_name
    if root_dir.exists() and root_dir.is_dir():
        return root_dir

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

                pyproject_path = pkg_root / "pyproject.toml"
                if not pyproject_path.exists():
                    continue

                try:
                    with open(pyproject_path, "rb") as f:
                        toml_data = tomllib.load(f)

                    package_name = toml_data.get("project", {}).get("name")
                    version = toml_data.get("project", {}).get("version")

                    if not package_name or not version:
                        logger.warning(f"Missing name or version in pyproject.toml for {pkg_root.name}")
                        continue

                    # Enforce strict naming matching the registry
                    module_path = get_expected_module_directory(pkg_root, package_name)
                    if not module_path:
                        logger.error(
                            f"Convention Violation: Package '{package_name}' does not contain a matching module folder inside '{pkg_root.name}'")
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
        if refresh or not self.discovered_packages:
            self.discovered_packages.clear()
            self._discover_local_packages()
            self._discover_registry_packages()
        return self.discovered_packages

    def _extract_plugin_classes(self, module) -> Dict[str, Type[BaseEvaluationPlugin]]:
        found_plugins = {}
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseEvaluationPlugin) and obj is not BaseEvaluationPlugin:
                found_plugins[obj.__name__] = obj
        return found_plugins

    def load_package(self, package_name: str, version: str) -> Dict[str, BaseEvaluationPlugin]:
        """Installs/Imports the package module, verifying convention criteria."""
        if not self.discovered_packages:
            self.list_packages()

        if package_name not in self.discovered_packages:
            raise KeyError(f"Package '{package_name}' not found.")

        available_versions = self.discovered_packages[package_name]
        if version not in available_versions:
            raise KeyError(f"Version '{version}' of package '{package_name}' not found.")

        package_meta = available_versions[version]
        module_name = package_meta["module_name"]

        # Flush sys.modules to enforce a clean re-import
        if module_name in sys.modules:
            modules_to_remove = [m for m in sys.modules if m == module_name or m.startswith(f"{module_name}.")]
            for m in modules_to_remove:
                del sys.modules[m]

        # Configure environment paths depending on the source target
        if package_meta["source"] == "local":
            import_path = package_meta["import_path"]
            if import_path not in sys.path:
                sys.path.insert(0, import_path)
        elif package_meta["source"] == "registry":
            target = f'{package_name}=={version}'
            uv_install(target, extra_index_url=self.devpi_client.simple_index_url, no_deps=True)

        sys.path_importer_cache.clear()
        importlib.invalidate_caches()

        # Import the unified module target
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            raise ImportError(f"Failed to import convention module '{module_name}' for package '{package_name}': {e}")

        plugin_classes = self._extract_plugin_classes(module)
        if not plugin_classes:
            raise ValueError(f"No valid implementations inheriting from BaseEvaluationPlugin found in '{module_name}'")

        # Instantiate implementation instances and update the engine cache
        instances = {}
        for name, cls in plugin_classes.items():
            instance = cls()
            instances[name] = instance
            cache_key = f"{package_name}::{version}::{name}"
            self._loaded_plugins[cache_key] = instance

        return instances

    def load_plugin(self, package_name: str, plugin_name: str, version: str) -> BaseEvaluationPlugin:
        cache_key = f"{package_name}::{version}::{plugin_name}"
        if cache_key in self._loaded_plugins:
            return self._loaded_plugins[cache_key]

        plugins = self.load_package(package_name, version)
        if plugin_name not in plugins:
            raise KeyError(f"Plugin '{plugin_name}' not found in module package '{package_name}'")

        return plugins[plugin_name]