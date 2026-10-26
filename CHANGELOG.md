# Changelog

## 2026-10-26: plugin interface 0.4.0

- The pin is `aisc-plugin-interface>=0.4.0` (the version with `assesses`, `helpers` and `connections`). PyPI's latest is 0.3.0, so the lock takes the wheel under `vendor/`, built from the AISC repo's `shared/plugin-interface` by its `scripts/vendor-plugin-interface.sh`. Test: `tests/test_interface_pin.py`.

Check: `uv run --locked --with pytest python -m pytest -q -p no:cacheprovider`.

## 2026-10-14: review pass (branch pass/libs-2026-10-13)

Areas: `loader.py` (local plugin folders), README "How it works".

- Local plugins: when the configured folder (`PLUGIN_PATH`) and the default `plugins/` hold the same package and version, the configured one is used and the other is logged and skipped (before, the default folder silently won). Test: `tests/test_loader_local.py`.
- Local plugins: a plugin in a git worktree or submodule now records its new commit after a commit (the provenance cache watches the real git folder and the branch ref). Test: `tests/test_loader_local.py`.

Check: `uv run --with pytest python -m pytest -q -p no:cacheprovider` (37 passed). On a running stack, after the engine and worker pick up this version: a duplicate local plugin shows a "shadowed by" warning in their logs.
