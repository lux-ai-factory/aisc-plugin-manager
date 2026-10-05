"""What code a local plugin folder holds: a content digest and, for a git checkout, its commit.

Recorded when a local plugin is installed and when it runs, so a result can be traced back to
the exact code that produced it, even though a local folder can change at any time.
"""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

# Environments, caches and build output: not part of the plugin's source.
IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "build", "dist", ".pytest_cache",
                ".mypy_cache", ".ruff_cache", "node_modules", ".idea", ".vscode"}
IGNORED_SUFFIXES = (".egg-info", ".pyc")


def _source_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in IGNORED_DIRS and not d.endswith(IGNORED_SUFFIXES))
        for name in sorted(filenames):
            if not name.endswith(IGNORED_SUFFIXES):
                yield Path(dirpath) / name


def stat_signature(root: Path) -> tuple:
    """Cheap fingerprint (paths, sizes, modification times) to tell when to re-hash."""
    root = Path(root)
    return tuple((str(p.relative_to(root)), s.st_size, s.st_mtime_ns)
                 for p in _source_files(root) for s in [p.stat()])


def folder_digest(root: Path) -> str:
    """SHA-256 over every source file's relative path and content."""
    root = Path(root)
    digest = hashlib.sha256()
    for path in _source_files(root):
        rel = str(path.relative_to(root)).encode()
        digest.update(len(rel).to_bytes(8, "big") + rel)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def _git_dir(root: Path) -> Path | None:
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():                      # worktree or submodule: "gitdir: <path>"
        line = dot_git.read_text().strip()
        if line.startswith("gitdir:"):
            path = Path(line.split(":", 1)[1].strip())
            return path if path.is_absolute() else (root / path).resolve()
    return None


def _read_commit(git_dir: Path) -> str | None:
    head = (git_dir / "HEAD").read_text().strip()
    if not head.startswith("ref:"):
        return head or None                    # detached HEAD
    ref = head.split(":", 1)[1].strip()
    common = git_dir
    if (git_dir / "commondir").is_file():      # worktrees keep refs in the common dir
        common = (git_dir / (git_dir / "commondir").read_text().strip()).resolve()
    for base in (git_dir, common):
        if (base / ref).is_file():
            return (base / ref).read_text().strip()
    packed = common / "packed-refs"
    if packed.is_file():
        for line in packed.read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    return None


def git_state(root: Path) -> dict:
    """{"commit", "dirty"}; both None outside a checkout. The commit is read from the files, so
    it works without a git binary; "dirty" needs git and is None when it isn't installed."""
    root = Path(root)
    try:
        git_dir = _git_dir(root)
        if git_dir is None or not (git_dir / "HEAD").is_file():
            return {"commit": None, "dirty": None}
        commit = _read_commit(git_dir)
    except (OSError, UnicodeDecodeError):                 # an unreadable .git: no commit to name
        return {"commit": None, "dirty": None}
    dirty = None
    if shutil.which("git"):
        try:
            # core.fsmonitor off: a hook configured in the folder's own .git/config never runs
            result = subprocess.run(["git", "-c", "safe.directory=*", "-c", "core.fsmonitor=false", "-C", str(root),
                                     "status", "--porcelain"], capture_output=True, text=True, timeout=30)
        except (subprocess.TimeoutExpired, OSError):
            result = None
        if result is not None and result.returncode == 0:
            dirty = bool(result.stdout.strip())
    return {"commit": commit, "dirty": dirty}
