# AISC Plugin Manager

`aisc-plugin-manager` is the Python library that finds, installs and imports AISC evaluation plugins.
AISC (the AI Assessment Sandbox Configurator) runs an assessment in six steps: qualification,
control objectives, installing plugins (the evaluation tools), executing tests and addressing
controls, analysing results on the dashboard, and composing the report. In steps 3 and 4 the
execution engine (`aisc-backend`) lists the plugins a project can install and reads their forms, and
the evaluation worker (`aisc-eval-worker`) loads a plugin to run it. Both do it through the `Loader`
class of this library. Plugins themselves are written against
[aisc-plugin-interface](https://github.com/lux-ai-factory/aisc-plugin-interface).

It is a library, not a service: nothing here listens on a port.

## How it works

`Loader(local_plugin_path, registry_url, registry_index, registry_user=None, registry_password=None)`
knows two sources of plugin packages:

- **Local folders.** The folder given as `local_plugin_path`, then `plugins/` relative to the working
  directory (the same folder reached both ways is scanned once). Each subfolder with a
  `pyproject.toml` is accepted when it has a `name` and a `version`, depends on
  `aisc-plugin-interface`, keeps its code in a folder named after the package (dashes as
  underscores) under `src/` or at the root, and that module imports and holds at least one
  non-abstract `BaseEvaluationPlugin` subclass. A local package is listed as `<version>+local`, so it
  never shadows the same version on the index. Its entry also records a SHA-256 digest of its source
  files, the git commit and whether the checkout is dirty (`digest.py`), and the
  `[tool.aisc] catalogue_slug` from its `pyproject.toml`, so a result can be traced to the exact code.
- **A devpi package index** (the stack's `devpi` service, which the catalogue publishes plugins to).
  `DevpiClient` lists the index's packages and their latest versions; it logs in only when a user and
  password are both non-empty.

Main calls:

| Call | What it does |
|---|---|
| `list_packages(refresh=False)` | `{package: {version: metadata}}` from both sources |
| `load_package(package, version)` | imports the package (an index package is first installed with `uv pip install --no-deps` into the running interpreter, with the index as extra index URL) and returns an instance of each plugin class, keyed by class name |
| `load_plugin(package, plugin_class_name, version)` | one plugin instance, cached |
| `refresh_package(package, version)` | drops the cached instances and loads again |

Modules in `src/aisc_plugin_manager/`: `loader.py` (`Loader`), `devpi_client.py` (`DevpiClient`),
`uv_client.py` (the `uv pip install` call), `digest.py` (folder digest and git state).

## Install

Requires Python 3.12 or newer, and the `uv` command on `PATH` to install plugins from the index.
Dependencies: `aisc-plugin-interface>=0.3.0`, `httpx`, `packaging` (and `pydantic`, through the
interface).

The package is not on PyPI. Install it from git:

```bash
uv add git+https://github.com/lux-ai-factory/aisc-plugin-manager --tag v0.3.0
# or the branch the AISC stack uses
uv add git+https://github.com/lux-ai-factory/aisc-plugin-manager --branch feat/unified-modules
```

The tag `v0.3.0` (the `master` branch) is what `apps/backend` and `apps/eval` pin in their
`pyproject.toml`. It does not have the `+local` versions, the digest and git state, or the fix that
skips the devpi login for empty credentials: those are only on `feat/unified-modules`. That branch's
`pyproject.toml` still says version 0.3.0.

### Inside the AISC stack

There is no compose service for this library. In `docker-compose.development.yml` (from the aisc
repo root) the services `aisc-backend`, `aisc-backend-migrate` and `aisc-eval-worker` mount `./shared`
and, on start, run `uv pip install --no-deps -e /app/shared/plugin-manager -e /app/shared/plugin-interface`,
so they use this checkout over the version baked into their image. It comes up with the stack: run
`./scripts/secrets.sh` once, then the `docker compose ... up` command in the aisc README. A change here
reaches those services when they restart. `docker-compose.engine-standalone.yml` does not mount
`./shared`, so the engine there uses the image's `v0.3.0`.

## Configuration

The library reads no environment variables: the caller passes everything to `Loader`. The engine and
the worker take these values from their environment (`apps/backend/config/settings.py`,
`apps/eval`), set in the compose files:

| Variable (in the engine and the worker) | `Loader` argument | Meaning |
|---|---|---|
| `PLUGIN_PATH` | `local_plugin_path` | Folder of local plugins (`./local_plugins/` in `env.runtime`). The compose files mount that host folder at `/app/plugins`, and the loader also scans `plugins/` under the working directory (`/app`), which is that mount |
| `PACKAGE_REGISTRY_URL` | `registry_url` | Base URL of the devpi server, for example `http://devpi:3141` |
| `PACKAGE_REGISTRY_INDEX` | `registry_index` | The index on that server, for example `root/public` |
| `PACKAGE_REGISTRY_USER`, `PACKAGE_REGISTRY_PASSWORD` | `registry_user`, `registry_password` | Credentials for a private index; leave both empty for a public one. Give both or neither |

## Tests

The tests build plugin packages in a temporary folder and stub the index; they need no database, no
network and no running stack. The project declares no dev dependencies, so pytest is added for the
run:

```bash
uv run --with pytest python -m pytest -q
```

The tests resolve `aisc-plugin-interface` 0.3.0 from PyPI through `uv.lock`.

## Layout

```text
src/aisc_plugin_manager/   the library
tests/                     loader (local discovery, +local versions) and digest tests
```

## Contributing

`feat/unified-modules` is the branch the AISC stack uses and the only one to work on. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the contributor licence terms.

## License

This project is licensed under the [Apache License 2.0](LICENSE.md).
© 2024–2026 Université du Luxembourg and Luxembourg Institute of Science and Technology (LIST).
