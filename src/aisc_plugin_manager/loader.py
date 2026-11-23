import importlib
import importlib.metadata
import inspect
import sys
import logging
import tomllib
from pathlib import Path
from typing import Dict, Type, Iterator

from packaging.version import parse as parse_version

from .devpi_client import DevpiClient
from .uv_client import uv_install

from aisc_plugin_interface import BaseEvaluationPlugin
from aisc_plugin_interface.adapters import (
    BUILTIN_ADAPTERS,
    PRESETS,
    DataAdapter,
    EndpointAdapter,
    Preset,
)

logger = logging.getLogger(__name__)
DEFAULT_PLUGIN_PATH = "plugins"
DEFAULT_BUNDLED_PLUGIN_PATH = "def_plugins"
AISC_INTERFACE_DEP = "aisc-plugin-interface"
#: The package name the standard adapter library is reported under.
BUILTIN_PACKAGE = "aisc-plugin-interface"


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
    def __init__(self, local_plugin_path: str, registry_url: str, registry_index: str, registry_user: str | None = None,
                 registry_password: str | None = None):
        # Skip an unset/empty path so we never end up scanning the working
        # directory as if it were a plugin folder.
        if local_plugin_path and local_plugin_path.strip():
            self.plugin_dirs = [Path(local_plugin_path)]
        else:
            self.plugin_dirs = []
        self.plugin_dirs += [
            Path(d) for d in (DEFAULT_PLUGIN_PATH, DEFAULT_BUNDLED_PLUGIN_PATH)
            if Path(d).is_dir() and Path(d) not in self.plugin_dirs
        ]
        self.devpi_client = DevpiClient(registry_url, registry_index, registry_user, registry_password)
        self.discovered_packages: Dict[str, Dict[str, dict]] = {}
        self.discovered_adapters: Dict[str, Dict[str, dict]] = {}
        self._loaded_plugins: Dict[str, BaseEvaluationPlugin] = {}

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
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

                    dependencies = toml_data.get("project", {}).get("dependencies", [])
                    if not any(AISC_INTERFACE_DEP in dep for dep in dependencies):
                        logger.warning(f"Skipping local package '{package_name}': does not depend on {AISC_INTERFACE_DEP}")
                        continue

                    # Enforce strict naming matching the registry
                    module_path = get_expected_module_directory(pkg_root, package_name)
                    if not module_path:
                        logger.error(
                            f"Convention Violation: Package '{package_name}' does not contain a matching module folder inside '{pkg_root.name}'")
                        continue

                    meta = {
                        "source": "local",
                        "pkg_root": pkg_root,
                        "module_name": module_path.name,
                        "import_path": str(module_path.parent.resolve())
                    }

                    classified = self._classify_module(meta)
                    if classified is None:
                        continue

                    plugin_classes, adapter_classes, preset_classes = classified
                    meta["plugin_classes"] = plugin_classes
                    meta["adapter_classes"] = adapter_classes
                    meta["preset_classes"] = preset_classes
                    meta["kinds"] = self._kinds_for(plugin_classes, adapter_classes)

                    if plugin_classes:
                        self.discovered_packages.setdefault(package_name, {})[version] = meta

                    if adapter_classes or preset_classes:
                        self.discovered_adapters.setdefault(package_name, {})[version] = meta
                except Exception as e:
                    logger.warning(f"Failed to read pyproject.toml for {pkg_root.name}: {e}")

    def _classify_module(self, package_meta: dict) -> tuple[list[type], list[type], list[type]] | None:
        """Imports a local package module and returns (plugins, adapters, presets).

        Returns ``None`` when the module cannot be imported. A broken adapter class
        is reported and skipped, not fatal.
        """
        module_name = package_meta["module_name"]
        import_path = package_meta["import_path"]

        old_path = sys.path[:]
        try:
            if import_path not in sys.path:
                sys.path.insert(0, import_path)

            sys.path_importer_cache.clear()
            importlib.invalidate_caches()

            try:
                module = importlib.import_module(module_name)
            except Exception as e:
                logger.warning(f"Failed to import local package '{module_name}': {e}")
                return None

            plugins = list(self._find_plugins_classes(module))
            adapters = list(self._find_adapter_classes(module))
            presets = list(self._find_preset_objects(module))
            return plugins, adapters, presets
        finally:
            sys.path = old_path

    def _discover_registry_packages(self):
        if not self.devpi_client:
            return
        try:
            registry_packages = self.devpi_client.list_packages()
            for package_name, versions in registry_packages.items():
                self.discovered_packages.setdefault(package_name, {})
                self.discovered_adapters.setdefault(package_name, {})

                if isinstance(versions, str):
                    versions = [versions]

                for version in versions:
                    # Classes are only knowable after install: advertise both kinds,
                    # with an empty manifest until the package is loaded.
                    registry_meta = {
                        "source": "registry",
                        "package": package_name,
                        "module_name": package_name.replace("-", "_"),
                        "plugin_classes": [],
                        "adapter_classes": [],
                        "preset_classes": [],
                        "kinds": ["plugin", "adapter"],
                    }
                    self.discovered_packages[package_name].setdefault(version, registry_meta)
                    self.discovered_adapters[package_name].setdefault(version, registry_meta)
        except Exception as e:
            logger.error(f"Failed to list registry plugins: {e}")

    def _refresh_discovery(self):
        self.discovered_packages.clear()
        self.discovered_adapters.clear()
        self._discover_local_packages()
        self._discover_registry_packages()

    @staticmethod
    def _filter_by_kind(packages: Dict[str, Dict[str, dict]], kind: str | None):
        if kind is None:
            return packages
        return {
            package_name: {
                version: meta
                for version, meta in versions.items()
                if kind in meta.get("kinds", [])
            }
            for package_name, versions in packages.items()
            if any(kind in meta.get("kinds", []) for meta in versions.values())
        }

    def list_packages(self, refresh: bool = False, kind: str | None = None) -> Dict[str, Dict[str, dict]]:
        if refresh or not self.discovered_packages:
            self._refresh_discovery()
        return self._filter_by_kind(self.discovered_packages, kind)

    def list_adapters(self, refresh: bool = False, kind: str | None = None) -> Dict[str, Dict[str, dict]]:
        """The adapter catalogue: builtin, local and (registry, until installed) packages.

        Shape: ``{package: {version: {"source": ..., "adapters": [manifest],
        "presets": [manifest]}}}``. A registry version carries empty lists until it
        is installed, because its classes are only knowable then.
        """
        if refresh or not self.discovered_adapters:
            self._refresh_discovery()
        catalogue: Dict[str, Dict[str, dict]] = {}

        builtin = self._builtin_catalogue()
        if builtin["adapters"] or builtin["presets"]:
            catalogue.setdefault(BUILTIN_PACKAGE, {})[builtin["version"]] = builtin

        for package_name, versions in self.discovered_adapters.items():
            for version, meta in versions.items():
                entry = self._catalogue_for(package_name, version, meta)
                catalogue.setdefault(package_name, {})[version] = entry

        return catalogue

    def _catalogue_for(self, package_name: str, version: str, meta: dict) -> dict:
        source = meta.get("source", "unknown")
        adapters: list[dict] = []
        presets: list[dict] = []
        if source == "local":
            for cls in meta.get("adapter_classes", []):
                manifest = self._adapter_manifest(
                    package_name, version, source, cls, meta.get("module_name", "")
                )
                if manifest is not None:
                    adapters.append(manifest)
            for preset_obj in meta.get("preset_classes", []):
                presets.append(self._preset_manifest(preset_obj))
        return {"source": source, "adapters": adapters, "presets": presets}

    def _builtin_catalogue(self) -> dict:
        try:
            version = importlib.metadata.version(BUILTIN_PACKAGE)
        except importlib.metadata.PackageNotFoundError:
            version = "0.0.0"
        adapters = [
            m
            for cls in BUILTIN_ADAPTERS
            if (
                m := self._adapter_manifest(
                    BUILTIN_PACKAGE, version, "builtin", cls, "aisc_plugin_interface.adapters"
                )
            )
        ]
        presets = [self._preset_manifest(p) for p in PRESETS.values()]
        return {"source": "builtin", "version": version, "adapters": adapters, "presets": presets}

    @staticmethod
    def _adapter_manifest(
        package_name: str, version: str, source: str, cls: type, module: str = ""
    ) -> dict | None:
        kind = "data" if issubclass(cls, DataAdapter) else "endpoint"
        try:
            config_schema = cls.config_schema()
        except Exception as e:  # a broken adapter is reported, not fatal
            logger.warning(f"Skipping adapter '{getattr(cls, 'id', cls.__name__)}' in '{package_name}': {e}")
            return None
        manifest = {
            "id": getattr(cls, "id", cls.__name__),
            "class": cls.__name__,
            "module": module,
            "label": getattr(cls, "label", ""),
            "kind": kind,
            "version": getattr(cls, "version", "1"),
            "config_schema": config_schema,
            "package": package_name,
            "package_version": version,
            "source": source,
        }
        if kind == "data":
            manifest["reads"] = list(getattr(cls, "reads", ()))
            manifest["produces"] = list(getattr(cls, "produces", ()))
        else:
            manifest["speaks"] = getattr(cls, "speaks", "")
            manifest["serves"] = list(getattr(cls, "serves", ()))
            manifest["passthrough"] = bool(getattr(cls, "passthrough", False))
            manifest["capabilities"] = dict(getattr(cls, "capabilities", {}))
            manifest["max_concurrency"] = int(getattr(cls, "max_concurrency", 8))
        return manifest

    @staticmethod
    def _preset_manifest(preset_obj: Preset) -> dict:
        return {
            "id": preset_obj.id,
            "label": preset_obj.label,
            "kind": preset_obj.kind,
            "protocol": preset_obj.protocol,
            "format": preset_obj.format,
            "adapter": preset_obj.adapter,
            "fields": [
                {
                    "key": f.key,
                    "label": f.label,
                    "target": f.target,
                    "config_key": f.config_key,
                    "type": f.type,
                    "required": f.required,
                    "secret": f.secret,
                    "default": f.default,
                }
                for f in preset_obj.fields
            ],
        }

    # ------------------------------------------------------------------ #
    # Class finding
    # ------------------------------------------------------------------ #
    def _find_plugins_classes(self, module) -> Iterator[Type[BaseEvaluationPlugin]]:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseEvaluationPlugin) and not getattr(obj.evaluate, "__isabstractmethod__", False):
                yield obj

    def _find_adapter_classes(self, module) -> Iterator[type]:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj in (DataAdapter, EndpointAdapter):
                continue
            if issubclass(obj, (DataAdapter, EndpointAdapter)) and not inspect.isabstract(obj):
                yield obj

    def _find_preset_objects(self, module) -> Iterator[Preset]:
        for _, obj in inspect.getmembers(module):
            if isinstance(obj, Preset):
                yield obj

    @staticmethod
    def _kinds_for(plugin_classes: list, adapter_classes: list) -> list[str]:
        kinds = []
        if plugin_classes:
            kinds.append("plugin")
        if adapter_classes:
            kinds.append("adapter")
        return kinds

    def _extract_plugin_classes(self, module) -> Dict[str, Type[BaseEvaluationPlugin]]:
        return {obj.__name__: obj for obj in self._find_plugins_classes(module)}

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def load_package(self, package_name: str, version: str | None = None) -> Dict[str, BaseEvaluationPlugin]:
        """Installs/Imports the package module, verifying convention criteria.

        An empty/None version resolves to the latest discovered version of the
        package (catalogue installs store no version until one is pinned).
        """
        if not self.discovered_packages:
            self.list_packages()

        available_versions = self.discovered_packages.get(package_name, {})
        if not available_versions:
            raise KeyError(f"Package '{package_name}' not found.")

        if not version:
            version = str(max(available_versions.keys(), key=parse_version))
        if version not in available_versions:
            raise KeyError(f"Version '{version}' of package '{package_name}' not found.")

        package_meta = available_versions[version]
        module = self._import_package(package_name, version, package_meta)

        plugin_classes = self._extract_plugin_classes(module)
        if not plugin_classes:
            raise ValueError(f"No valid implementations inheriting from BaseEvaluationPlugin found in '{package_meta['module_name']}'")

        # Instantiate implementation instances and update the engine cache
        instances = {}
        for name, cls in plugin_classes.items():
            instance = cls()
            instances[name] = instance
            cache_key = f"{package_name}::{version}::{name}"
            self._loaded_plugins[cache_key] = instance

        return instances

    def load_adapter(self, package_name: str, version: str, adapter_id: str) -> type:
        """Imports/installs a package and returns the adapter class for ``adapter_id``.

        The builtin standard library is addressed as package
        ``aisc-plugin-interface`` and resolved without an install.
        """
        if package_name == BUILTIN_PACKAGE:
            for cls in BUILTIN_ADAPTERS:
                if getattr(cls, "id", "") == adapter_id:
                    return cls
            raise KeyError(f"Builtin adapter '{adapter_id}' not found.")

        if not self.discovered_adapters:
            self.list_adapters()

        available_versions = self.discovered_adapters.get(package_name, {})
        if not available_versions:
            raise KeyError(f"Package '{package_name}' not found.")
        if version not in available_versions:
            raise KeyError(f"Version '{version}' of package '{package_name}' not found.")

        package_meta = available_versions[version]
        module = self._import_package(package_name, version, package_meta)

        for cls in self._find_adapter_classes(module):
            if getattr(cls, "id", "") == adapter_id:
                return cls
        raise KeyError(f"Adapter '{adapter_id}' not found in package '{package_name}'")

    def _import_package(self, package_name: str, version: str, package_meta: dict):
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

        try:
            return importlib.import_module(module_name)
        except ImportError as e:
            raise ImportError(f"Failed to import convention module '{module_name}' for package '{package_name}': {e}")

    def refresh_package(self, package_name: str, version: str) -> Dict[str, BaseEvaluationPlugin]:
        cache_keys = [k for k in self._loaded_plugins if k.startswith(f"{package_name}::{version}::")]
        for k in cache_keys:
            del self._loaded_plugins[k]
        return self.load_package(package_name, version)

    def load_plugin(self, package_name: str, plugin_name: str, version: str) -> BaseEvaluationPlugin:
        cache_key = f"{package_name}::{version}::{plugin_name}"
        if cache_key in self._loaded_plugins:
            return self._loaded_plugins[cache_key]

        plugins = self.load_package(package_name, version)
        if plugin_name not in plugins:
            raise KeyError(f"Plugin '{plugin_name}' not found in module package '{package_name}'")

        return plugins[plugin_name]
