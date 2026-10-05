import importlib
import inspect
import sys
import logging
import tomllib
from pathlib import Path
from typing import Dict, Type, Iterator

from .devpi_client import DevpiClient
from .digest import folder_digest, git_state, stat_signature
from .uv_client import uv_install

from aisc_plugin_interface import BaseEvaluationPlugin

logger = logging.getLogger(__name__)
DEFAULT_PLUGIN_PATH = "plugins"
AISC_INTERFACE_DEP = "aisc-plugin-interface"
LOCAL_LABEL = "local"


def _git_marks(pkg_root: Path) -> tuple:
    """The mtimes of a checkout's HEAD and index: they move with a commit, a checkout or staging."""
    marks = []
    for name in ("HEAD", "index"):
        path = Path(pkg_root) / ".git" / name
        marks.append(path.stat().st_mtime_ns if path.is_file() else None)
    return tuple(marks)


def local_version(version: str) -> str:
    """The version a local package is listed under: PEP 440 local label "+local" (".local" when
    the version already has a local label), so it never shadows a registry version."""
    return f"{version}.{LOCAL_LABEL}" if "+" in version else f"{version}+{LOCAL_LABEL}"


def get_expected_module_directory(pkg_root: Path, package_name: str) -> Path | None:
    """
    The package's module folder: ``src/<module>/`` or ``<module>/`` at the project root, where
    ``<module>`` is the package name with dashes turned into underscores. None when neither exists.
    """
    module_name = package_name.replace("-", "_")

    src_dir = pkg_root / "src" / module_name
    if src_dir.exists() and src_dir.is_dir():
        return src_dir

    root_dir = pkg_root / module_name
    if root_dir.exists() and root_dir.is_dir():
        return root_dir

    return None


class Loader:
    def __init__(self, local_plugin_path: str, registry_url: str, registry_index: str, registry_user: str | None = None,
                 registry_password: str | None = None):
        # The configured folder first, then the conventional one relative to the working
        # directory (deployments whose PLUGIN_PATH is a host path still find the mount there).
        # The same folder reached both ways is scanned once.
        self.plugin_dirs = []
        for candidate in (Path(local_plugin_path), Path(DEFAULT_PLUGIN_PATH)):
            if not any(self._same_dir(candidate, known) for known in self.plugin_dirs):
                self.plugin_dirs.append(candidate)
        self.devpi_client = DevpiClient(registry_url, registry_index, registry_user, registry_password)
        self.discovered_packages: Dict[str, Dict[str, dict]] = {}
        self._loaded_plugins: Dict[str, BaseEvaluationPlugin] = {}
        self._digest_cache: Dict[str, tuple] = {}

    @staticmethod
    def _same_dir(a: Path, b: Path) -> bool:
        try:
            return a.resolve() == b.resolve()
        except OSError:
            return False

    def _provenance(self, pkg_root: Path) -> dict:
        """{"digest", "git_commit", "git_dirty"} of a local folder, each None when it can't be read: a
        plugin is never dropped for its provenance. Recomputed only when a source file's path, size
        or mtime, or the checkout's HEAD or index, changed."""
        key = str(pkg_root.resolve())
        try:
            signature = (stat_signature(pkg_root), _git_marks(pkg_root))
        except OSError as exc:
            logger.warning(f"Local package at {pkg_root}: its files could not be listed ({exc}); no digest")
            return {"digest": None, "git_commit": None, "git_dirty": None}
        cached = self._digest_cache.get(key)
        if cached and cached[0] == signature:
            return dict(cached[1])
        try:
            digest = folder_digest(pkg_root)
        except OSError as exc:
            logger.warning(f"Local package at {pkg_root}: a file could not be read ({exc}); no digest")
            digest = None
        facts = {"digest": digest, **{f"git_{k}": v for k, v in git_state(pkg_root).items()}}
        if digest is not None:
            self._digest_cache[key] = (signature, facts)
        return dict(facts)

    def _discover_local_packages(self):
        for plugin_dir in self.plugin_dirs:
            if not plugin_dir.exists() or not plugin_dir.is_dir():
                logger.info(f"Local plugin folder {plugin_dir} does not exist; skipped")
                continue
            logger.info(f"Scanning local plugin folder {plugin_dir.resolve()}")
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

                    # Same naming rule as for a package installed from the index
                    module_path = get_expected_module_directory(pkg_root, package_name)
                    if not module_path:
                        logger.error(
                            f"Convention Violation: Package '{package_name}' does not contain a matching module folder inside '{pkg_root.name}'")
                        continue

                    catalogue_slug = toml_data.get("tool", {}).get("aisc", {}).get("catalogue_slug")
                    meta = {
                        "source": "local",
                        "pkg_root": pkg_root,
                        "module_name": module_path.name,
                        "import_path": str(module_path.parent.resolve()),
                        "declared_version": version,
                        "catalogue_slug": catalogue_slug or None,
                    }

                    if self._is_package_valid(meta):
                        meta.update(self._provenance(pkg_root))
                        if package_name not in self.discovered_packages:
                            self.discovered_packages[package_name] = {}

                        self.discovered_packages[package_name][local_version(version)] = meta
                except Exception as e:
                    logger.warning(f"Failed to read pyproject.toml for {pkg_root.name}: {e}")

    def _is_package_valid(self, package_meta: dict) -> bool:
        """Checks if a local package contains at least one BaseEvaluationPlugin implementation."""
        if package_meta["source"] != "local":
            return True

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
                return next(self._find_plugins_classes(module), None) is not None
            except Exception as e:
                logger.warning(f"Failed to validate local package '{module_name}': {e}")
                return False
        finally:
            sys.path = old_path

    def _discover_registry_packages(self):
        if not self.devpi_client:
            return
        try:
            registry_packages = self.devpi_client.list_versions()
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

    def _find_plugins_classes(self, module) -> Iterator[Type[BaseEvaluationPlugin]]:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseEvaluationPlugin) and not getattr(obj.evaluate, "__isabstractmethod__", False):
                yield obj

    def _extract_plugin_classes(self, module) -> Dict[str, Type[BaseEvaluationPlugin]]:
        return {obj.__name__: obj for obj in self._find_plugins_classes(module)}

    def load_package(self, package_name: str, version: str) -> Dict[str, BaseEvaluationPlugin]:
        """Import a package (installing it first when it comes from the index) and return an
        instance of each plugin class in it, keyed by class name."""
        if not self.discovered_packages:
            self.list_packages()

        if package_name not in self.discovered_packages:
            raise KeyError(f"Package '{package_name}' not found.")

        available_versions = self.discovered_packages[package_name]
        if version not in available_versions and local_version(version) in available_versions:
            # a local plugin stored under its declared version, from before "+local" listing
            version = local_version(version)
        if version not in available_versions:
            raise KeyError(f"Version '{version}' of package '{package_name}' not found.")

        package_meta = available_versions[version]
        module_name = package_meta["module_name"]

        # Drop the module and its submodules from sys.modules so the import reads the code afresh
        if module_name in sys.modules:
            modules_to_remove = [m for m in sys.modules if m == module_name or m.startswith(f"{module_name}.")]
            for m in modules_to_remove:
                del sys.modules[m]

        # A local package is imported from its folder; an index package is installed first, without
        # its dependencies.
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
            module = importlib.import_module(module_name)
        except ImportError as e:
            raise ImportError(f"Failed to import convention module '{module_name}' for package '{package_name}': {e}")

        plugin_classes = self._extract_plugin_classes(module)
        if not plugin_classes:
            raise ValueError(f"No valid implementations inheriting from BaseEvaluationPlugin found in '{module_name}'")

        instances = {}
        for name, cls in plugin_classes.items():
            instance = cls()
            instances[name] = instance
            cache_key = f"{package_name}::{version}::{name}"
            self._loaded_plugins[cache_key] = instance

        return instances

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
