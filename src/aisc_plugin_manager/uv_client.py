import sys
import subprocess

def uv_install(target: str, extra_index_url: str = None, no_deps: bool = False):
    cmd = ["uv", "pip", "install", "--python", sys.executable]

    cmd.append(target)

    if extra_index_url:
        cmd.extend(["--extra-index-url", extra_index_url])

    if no_deps:
        cmd.append("--no-deps")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"uv pip install failed: {result.stderr}")

    return result.stdout