# AISC Plugin Manager

`aisc-plugin-manager` is the Python library that finds, installs and imports AISC tests (evaluation plugins).
AISC (the AI Assessment Sandbox Configurator) runs an assessment in four steps: 1 Qualify the AI system, 2 Set
control objectives, 3 Choose tests and checklists, 4 Collect evidence. The engine uses this library for the
tests installed in step 3 and run in step 4: `aisc-backend` lists the test packages and reads their forms, and
`aisc-eval-worker` finds the package of a test it runs. Both do it through the `Loader` class of this library.
Plugins themselves are written against
[aisc-plugin-interface](https://github.com/lux-ai-factory/aisc-plugin-interface).

It is a library, not a service: nothing here listens on a port.

## How it works

`Loader(local_plugin_path, registry_url, registry_index, registry_user=None, registry_password=None)`
knows two sources of plugin packages:

- **Local folders.** The folder given as `local_plugin_path`, then `plugins/` relative to the working
  directory (the same folder reached both ways is scanned once). When both folders hold the same
  package and version, the configured folder's copy is used; the other is logged with both paths
  and skipped. Each subfolder with a
  `pyproject.toml` is accepted when it has a `name` and a `version`, depends on
  `aisc-plugin-interface`, keeps its code in a folder named after the package (dashes as
  underscores) under `src/` or at the root, and that module imports and holds at least one
  non-abstract `BaseEvaluationPlugin` subclass. A local package is listed as `<version>+local`, so it
  never shadows the same version on the index; `load_package` also finds it by its declared version.
  Its entry also records a SHA-256 digest of its source files, the git commit and whether the
  checkout is dirty (`digest.py`), and the `[tool.aisc] catalogue_slug` from its `pyproject.toml`, so a
  result can be traced to the exact code. Those are cached until a file or the checkout changes (its
  HEAD, index or current branch ref; a git worktree or submodule, whose `.git` is a file, is followed), and
  are None when they can't be read: a plugin is never dropped for them.
- **A devpi package index** (the stack's `devpi` service, where `plugin-publisher` puts each test an admin
  installs from the project's catalogue). `DevpiClient.list_versions` lists every version of each package (the
  loader offers whichever a project has), `list_packages` the latest of each; it logs in only when a user and
  password are both non-empty.

In the AISC stack, a plugin of your own goes in `local_plugins/` of the aisc repository and is installed from a
private catalogue like a public test (see `local_plugins/README.md` there). For how tests are installed and run,
see `docs/architecture.md` ("Running tests") in the aisc repository.

Main calls:

| Call | What it does |
|---|---|
| `list_packages(refresh=False)` | `{package: {version: metadata}}` from both sources |
| `load_package(package, version)` | imports the package (an index package is first installed with `uv pip install --no-deps` into the running interpreter, with the index as extra index URL) and returns an instance of each plugin class, keyed by class name |
| `load_plugin(package, plugin_class_name, version)` | one plugin instance, cached |
| `refresh_package(package, version)` | drops the cached instances and loads again |

`load_package` is how `aisc-backend` reads a test's form. The worker does not run a test in its own
interpreter: it installs the package with its dependencies in a fresh environment (see `apps/eval`).

Modules in `src/aisc_plugin_manager/`: `loader.py` (`Loader`), `devpi_client.py` (`DevpiClient`),
`uv_client.py` (the `uv pip install` call), `digest.py` (folder digest and git state).

## Install

Requires Python 3.12 or newer, and the `uv` command on `PATH` to install plugins from the index.
Dependencies: `aisc-plugin-interface>=0.4.0`, `httpx`, `packaging` (and `pydantic`, through the
interface). The lock takes the interface from the wheel under `vendor/` (PyPI has no 0.4.0), built by the aisc
repo's `scripts/vendor-plugin-interface.sh`.

The package is not on PyPI. Install it from git:

```bash
uv add git+https://github.com/lux-ai-factory/aisc-plugin-manager --branch pilot
# or the tag of the master branch's release
uv add git+https://github.com/lux-ai-factory/aisc-plugin-manager --tag v0.3.0
```

This branch's `pyproject.toml` says version `0.3.0+aisc.1`. The tag `v0.3.0` (the `master` branch) does not
have the `+local` versions, the digest and git state, the fix that skips the devpi login for empty credentials,
or the 0.4.0 interface. `apps/backend` and `apps/eval` do not install from git: their locks take a
`aisc_plugin_manager-0.3.0+aisc.1` wheel under their `vendor/`, built from this checkout by the aisc repo's
`scripts/vendor-plugin-interface.sh`.

### Inside the AISC stack

There is no compose service for this library. In `docker-compose.development.yml` (from the aisc
repo root) the engine's services (`aisc-backend`, `aisc-backend-migrate`, `aisc-backend-ledger`,
`aisc-eval-worker`, `aisc-eval-flower`) mount `./shared` and, on start, run
`uv pip install --no-deps -e /app/shared/plugin-manager -e /app/shared/plugin-interface`, so they use this
checkout over the version in their image. It comes up with the stack (`uv run scripts/aisc.py start`). A change
here reaches those services when they restart. `docker-compose.engine-standalone.yml` does not mount
`shared/plugin-manager`, so the engine there uses the vendored wheel in its image.

## Configuration

The library reads no environment variables: the caller passes everything to `Loader`. The engine and
the worker take these values from their environment (`apps/backend/config/settings.py`,
`apps/eval/aisc_eval/utils/env.py`), set in the compose files:

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

The tests resolve `aisc-plugin-interface` 0.4.0 from the wheel under `vendor/` through `uv.lock`;
`tests/test_interface_pin.py` checks that the lock never takes it from PyPI.

## Layout

```text
src/aisc_plugin_manager/   the library
tests/                     loader (local discovery, +local versions), index versions, digest and interface pin tests
```

## Contributing

`pilot` is the branch the AISC stack uses and the only one to work on. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the contributor licence terms.

## License

This project is licensed under the [Apache License 2.0](LICENSE.md).
© 2024–2026 Université du Luxembourg and Luxembourg Institute of Science and Technology (LIST).
