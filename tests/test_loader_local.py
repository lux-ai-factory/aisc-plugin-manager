"""Local plugins: the folders scanned, +local versions, digest and git state."""
import logging

import pytest

from aisc_plugin_manager import devpi_client
from aisc_plugin_manager.loader import Loader
from conftest import make_plugin


def loader(path, **kw):
    return Loader(str(path), "http://devpi.invalid", "root/public", **kw)


# discovery of a valid package

def test_a_valid_local_plugin_is_discovered(plugin_root, no_registry):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0")
    packages = loader(plugin_root).list_packages(refresh=True)
    assert "aisc-plugin-demo" in packages


@pytest.mark.parametrize("kind", ["no_interface_dependency", "wrong_module_folder", "no_plugin_class"])
def test_invalid_local_folders_are_skipped(plugin_root, no_registry, kind):
    if kind == "no_interface_dependency":
        make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0", depends=False)
    elif kind == "wrong_module_folder":
        pkg = make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0")
        (pkg / "aisc_plugin_demo").rename(pkg / "something_else")
    else:
        make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0", module_code="X = 1\n")
    assert loader(plugin_root).list_packages(refresh=True) == {}


def test_a_local_plugin_loads_its_classes(plugin_root, no_registry):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0", cls="DemoPlugin")
    ld = loader(plugin_root)
    version = next(iter(ld.list_packages(refresh=True)["aisc-plugin-demo"]))
    assert set(ld.load_package("aisc-plugin-demo", version)) == {"DemoPlugin"}


# scanned folders

def test_l1_found_through_the_configured_path(plugin_root, no_registry):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0")
    assert "aisc-plugin-demo" in loader(plugin_root).list_packages(refresh=True)


def test_l1_found_through_the_fallback_when_the_configured_path_is_missing(tmp_path, monkeypatch, no_registry):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "plugins").mkdir()
    make_plugin(tmp_path / "plugins", "demo", "aisc-plugin-demo", "0.1.0")
    assert "aisc-plugin-demo" in loader(tmp_path / "does-not-exist").list_packages(refresh=True)


def test_l1_the_same_folder_through_both_paths_is_scanned_once(tmp_path, monkeypatch, no_registry):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "plugins").mkdir()
    make_plugin(tmp_path / "plugins", "demo", "aisc-plugin-demo", "0.1.0")
    ld = loader(tmp_path / "plugins")
    assert len(ld.plugin_dirs) == 1
    assert list(ld.list_packages(refresh=True)["aisc-plugin-demo"]) == ["0.1.0+local"]


def test_l1_scanned_and_missing_folders_are_logged(tmp_path, monkeypatch, caplog, no_registry):
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.INFO, logger="aisc_plugin_manager.loader"):
        loader(tmp_path / "nowhere").list_packages(refresh=True)
    text = caplog.text
    assert "nowhere" in text and "does not exist" in text


# +local versions

def test_l5_local_is_listed_as_plus_local_next_to_the_registry_version(plugin_root, monkeypatch):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.1")
    monkeypatch.setattr(devpi_client.DevpiClient, "list_versions", lambda self: {"aisc-plugin-demo": ["0.1.1"]})
    versions = loader(plugin_root).list_packages(refresh=True)["aisc-plugin-demo"]
    assert versions["0.1.1+local"]["source"] == "local"
    assert versions["0.1.1"]["source"] == "registry"


def test_l5_a_version_that_already_has_a_local_label_gets_dot_local(plugin_root, no_registry):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.1+abc")
    assert list(loader(plugin_root).list_packages(refresh=True)["aisc-plugin-demo"]) == ["0.1.1+abc.local"]


def test_l5_metadata_keeps_the_declared_version_and_catalogue_slug(plugin_root, no_registry):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.2.0", tool_aisc='catalogue_slug = "demo-entry"')
    meta = loader(plugin_root).list_packages(refresh=True)["aisc-plugin-demo"]["0.2.0+local"]
    assert meta["declared_version"] == "0.2.0"
    assert meta["catalogue_slug"] == "demo-entry"
    assert len(meta["digest"]) == 64


def test_l5_without_tool_aisc_the_catalogue_slug_is_none(plugin_root, no_registry):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.2.0")
    meta = loader(plugin_root).list_packages(refresh=True)["aisc-plugin-demo"]["0.2.0+local"]
    assert meta["catalogue_slug"] is None


def test_l5_a_plus_local_version_loads(plugin_root, monkeypatch):
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.1")
    monkeypatch.setattr(devpi_client.DevpiClient, "list_packages", lambda self: {"aisc-plugin-demo": ["0.1.1"]})
    ld = loader(plugin_root)
    ld.list_packages(refresh=True)
    assert "DemoPlugin" in ld.load_package("aisc-plugin-demo", "0.1.1+local")


# ── code review 2026-10-05 ──────────────────────────────────────────────────

def test_a_digest_that_fails_leaves_the_plugin_listed(plugin_root, no_registry, monkeypatch):
    """The digest and git state ran inside the try that skips a package, so an unreadable file or a
    slow git dropped a valid local plugin, logged as a pyproject.toml failure."""
    from aisc_plugin_manager import loader as loader_module

    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0")
    monkeypatch.setattr(loader_module, "folder_digest", lambda root: (_ for _ in ()).throw(OSError("unreadable")))
    versions = loader(plugin_root).list_packages(refresh=True)["aisc-plugin-demo"]
    assert versions["0.1.0+local"]["digest"] is None


def test_a_git_status_that_times_out_says_nothing_of_dirtiness(tmp_path, monkeypatch):
    import subprocess

    from aisc_plugin_manager import digest

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 30)

    monkeypatch.setattr(digest.subprocess, "run", slow)
    monkeypatch.setattr(digest.shutil, "which", lambda name: "/usr/bin/git")
    assert digest.git_state(tmp_path) == {"commit": "0123456789abcdef0123456789abcdef01234567", "dirty": None}


def test_git_status_is_not_run_again_while_nothing_changed(plugin_root, no_registry, monkeypatch):
    """Every listing ran git status and re-walked every local plugin: only the hash was cached."""
    from aisc_plugin_manager import loader as loader_module

    pkg = make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0")
    (pkg / ".git").mkdir()
    (pkg / ".git" / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")
    calls = []
    real = loader_module.git_state
    monkeypatch.setattr(loader_module, "git_state", lambda root: calls.append(root) or real(root))
    ld = loader(plugin_root)
    ld.list_packages(refresh=True)
    ld.list_packages(refresh=True)
    assert len(calls) == 1


def test_a_local_plugin_stored_under_its_declared_version_still_loads(plugin_root, no_registry):
    """Local packages became '<version>+local'; a plugin the engine stored as '0.1.0' before that was
    'not found' on every page."""
    make_plugin(plugin_root, "demo", "aisc-plugin-demo", "0.1.0", cls="DemoPlugin")
    ld = loader(plugin_root)
    ld.list_packages(refresh=True)
    assert set(ld.load_package("aisc-plugin-demo", "0.1.0")) == {"DemoPlugin"}
