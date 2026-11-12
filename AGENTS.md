# AGENTS.md — aisc-plugin-manager

A **published ABI**: the package loader plugins are discovered and installed through.

## Read first

The binding rules live in the parent repository, `lux-ai-factory/aisc`:
`ARCHITECTURE.md` (invariants I1–I11) and `docs/refactor-plan.md`.

## What binds this repo

- **I3** — it may depend on `aisc-plugin-interface`; it must not depend on the engine
  (`aisc_backend`, `aisc_eval`).

## Build and test

```bash
uv sync
uv run ruff check .
uv build
```