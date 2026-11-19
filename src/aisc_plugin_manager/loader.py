import logging
import tomllib
from pathlib import Path
from typing import Dict

from .devpi_client import DevpiClient
from .digest import _git_dir, folder_digest, git_state, stat_signature

logger = logging.getLogger(__name__)
DEFAULT_PLUGIN_PATH = "plugins"
AISC_INTERFACE_DEP = "aisc-plugin-interface"
LOCAL_LABEL = "local"
CODE_NOT_IMPORTED = "plugin code is not imported in this process: read the plugin's stored manifest"


class CodeNotImported(RuntimeError):
    """A call that would import plugin code in the calling process."""


def _git_marks(pkg_root: Path) -> tuple:
    """The mtimes of a checkout's HEAD, index and current branch ref: they move with a commit, a
    checkout or staging. The git folder is found as digest finds it, so a worktree or submodule
    (".git" is a file pointing elsewhere) is followed too."""
    git_dir = _git_dir(Path(pkg_root))
    if git_dir is None:
        return (None, None, None)
    paths = [git_dir / "HEAD", git_dir / "index"]
    head = paths[0].read_text().strip() if paths[0].is_file() else ""
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        common = git_dir
        if (git_dir / "commondir").is_file():      # worktrees keep refs in the common dir
            common = (git_dir / (git_dir / "commondir").read_text().strip()).resolve()
        paths.append(next((base / ref for base in (git_dir, common) if (base / ref).is_file()), common / "packed-refs"))
    return tuple(path.stat().st_mtime_ns if path.is_file() else None for path in paths)


DIMENSION_IDS = tuple(f"REQ{n}" for n in range(1, 12))


def _declared_dimensions(value) -> list[str]:
    """The trustworthiness dimensions (REQ1..REQ11) a package declares in [tool.aisc]; anything else is ignored."""
    if not isinstance(value, list):
        return []
    return [d for d in DIMENSION_IDS if d in value]


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
        # The same folder reached both ways is scanned once. A package and version found in both
        # is taken from the configured folder; the other copy is logged and skipped.
        self.plugin_dirs = []
        for candidate in (Path(local_plugin_path), Path(DEFAULT_PLUGIN_PATH)):
            if not any(self._same_dir(candidate, known) for known in self.plugin_dirs):
                self.plugin_dirs.append(candidate)
        self.devpi_client = DevpiClient(registry_url, registry_index, registry_user, registry_password)
        self.discovered_packages: Dict[str, Dict[str, dict]] = {}
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

                    tool_aisc = toml_data.get("tool", {}).get("aisc", {})
                    catalogue_slug = tool_aisc.get("catalogue_slug")
                    meta = {
                        "source": "local",
                        "pkg_root": pkg_root,
                        "module_name": module_path.name,
                        "import_path": str(module_path.parent.resolve()),
                        "declared_version": version,
                        "catalogue_slug": catalogue_slug or None,
                        "dimensions": _declared_dimensions(tool_aisc.get("dimensions")),
                    }

                    if self._is_package_valid(meta):
                        meta.update(self._provenance(pkg_root))
                        if package_name not in self.discovered_packages:
                            self.discovered_packages[package_name] = {}

                        listed = self.discovered_packages[package_name]
                        known = listed.get(local_version(version))
                        if known is not None:       # the folder scanned first (the configured one) wins
                            logger.warning(f"Local package '{package_name}' {version} at {pkg_root.resolve()} is "
                                           f"shadowed by the one at {Path(known['pkg_root']).resolve()}; skipped")
                        else:
                            listed[local_version(version)] = meta
                except Exception as e:
                    logger.warning(f"Failed to read pyproject.toml for {pkg_root.name}: {e}")

    def _is_package_valid(self, package_meta: dict) -> bool:
        """Whether a local folder is a plugin package, from its files alone: its module folder holds an
        __init__.py (the dependency on the plugin interface is checked before). Nothing of it is imported:
        its plugin classes are read in the worker's sandbox, never in the calling process."""
        if package_meta["source"] != "local":
            return True
        init = Path(package_meta["import_path"]) / package_meta["module_name"] / "__init__.py"
        if not init.is_file():
            logger.warning(f"Skipping local package '{package_meta['module_name']}': its module folder has no "
                           f"__init__.py")
            return False
        return True

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

    def package_meta(self, package_name: str, version: str) -> dict:
        """What the listing knows of one version of a package (source, module name, and for a local folder its
        path and provenance). A local plugin stored under its declared version, from before "+local" listing,
        is found too. KeyError when it is not listed."""
        if not self.discovered_packages:
            self.list_packages()
        if package_name not in self.discovered_packages:
            raise KeyError(f"Package '{package_name}' not found.")
        available_versions = self.discovered_packages[package_name]
        if version not in available_versions and local_version(version) in available_versions:
            version = local_version(version)
        if version not in available_versions:
            raise KeyError(f"Version '{version}' of package '{package_name}' not found.")
        return available_versions[version]

    def local_provenance(self, package_name: str, version: str) -> dict | None:
        """{"digest", "git_commit", "git_dirty"} of a listed local plugin's folder as it is now (recomputed only
        when a file or the checkout changed), None for a version that is not a local folder."""
        try:
            meta = self.package_meta(package_name, version)
        except KeyError:
            return None
        if meta.get("source") != "local":
            return None
        return self._provenance(Path(meta["pkg_root"]))

    def load_package(self, package_name: str, version: str):
        """Refused: plugin code is never imported in the calling process (the engine). The worker reads a
        plugin's manifest in its sandbox, and the engine answers from the manifest stored on the plugin."""
        raise CodeNotImported(CODE_NOT_IMPORTED)

    def refresh_package(self, package_name: str, version: str):
        """Refused, as load_package."""
        raise CodeNotImported(CODE_NOT_IMPORTED)

    def load_plugin(self, package_name: str, plugin_name: str, version: str):
        """Refused, as load_package."""
        raise CodeNotImported(CODE_NOT_IMPORTED)
