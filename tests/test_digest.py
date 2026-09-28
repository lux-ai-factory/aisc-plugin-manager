"""L6: the folder digest and git state recorded for a local plugin."""
import subprocess

import pytest

from aisc_plugin_manager.digest import folder_digest, git_state
from conftest import make_plugin


@pytest.fixture
def pkg(tmp_path):
    return make_plugin(tmp_path, "demo", "aisc-plugin-demo", "0.1.0")


def test_l6_digest_is_stable(pkg):
    assert folder_digest(pkg) == folder_digest(pkg)
    assert len(folder_digest(pkg)) == 64


def test_l6_digest_changes_when_a_source_file_changes(pkg):
    before = folder_digest(pkg)
    (pkg / "aisc_plugin_demo" / "__init__.py").write_text("changed\n")
    assert folder_digest(pkg) != before


def test_l6_digest_changes_when_a_file_is_added_or_renamed(pkg):
    before = folder_digest(pkg)
    (pkg / "extra.txt").write_text("x")
    added = folder_digest(pkg)
    (pkg / "extra.txt").rename(pkg / "other.txt")
    assert len({before, added, folder_digest(pkg)}) == 3


@pytest.mark.parametrize("ignored", [".venv", "__pycache__", ".git", "build", "dist",
                                     "x.egg-info", ".pytest_cache", "node_modules"])
def test_l6_digest_ignores_environments_caches_and_build_output(pkg, ignored):
    before = folder_digest(pkg)
    (pkg / ignored).mkdir()
    (pkg / ignored / "junk").write_text("junk")
    (pkg / "aisc_plugin_demo" / "__pycache__").mkdir(exist_ok=True)
    (pkg / "aisc_plugin_demo" / "__pycache__" / "m.pyc").write_bytes(b"\0")
    assert folder_digest(pkg) == before


def test_l6_git_state_of_a_non_checkout_is_empty(pkg):
    assert git_state(pkg) == {"commit": None, "dirty": None}


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd), "PATH": "/usr/bin:/bin"})


def test_l6_git_commit_and_dirty_flag_of_a_checkout(pkg):
    _git(pkg, "init", "-q", "-b", "main")
    _git(pkg, "add", "-A")
    _git(pkg, "commit", "-q", "-m", "c")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=pkg, capture_output=True, text=True).stdout.strip()
    assert git_state(pkg) == {"commit": head, "dirty": False}
    (pkg / "aisc_plugin_demo" / "__init__.py").write_text("changed\n")
    assert git_state(pkg) == {"commit": head, "dirty": True}


def test_l6_commit_is_read_without_a_git_binary(pkg, monkeypatch):
    _git(pkg, "init", "-q", "-b", "main")
    _git(pkg, "add", "-A")
    _git(pkg, "commit", "-q", "-m", "c")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=pkg, capture_output=True, text=True).stdout.strip()
    import aisc_plugin_manager.digest as digest
    monkeypatch.setattr(digest.shutil, "which", lambda name: None)
    assert git_state(pkg) == {"commit": head, "dirty": None}
