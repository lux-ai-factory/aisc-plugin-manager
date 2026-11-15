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
  underscores) under `src/` or at the root, and that folder holds an `__init__.py`. Nothing of it is imported:
  its plugin classes are read by the worker, in its sandbox. A local package is listed as `<version>+local`, so it
  never shadows the same version on the index; `package_meta` also finds it by its declared version.
  Its entry also records a SHA-256 digest of its source files, the git commit and whether the
  checkout is dirty (`digest.py`), and the `[tool.aisc] catalogue_slug` from its `pyproject.toml`, so a
  result can be traced to the exact code. Those are cached until a file or the checkout changes (its
  HEAD, index or current branch ref; a git worktree or submodule, whose `.git` is a file, is followed), and
  are None when they can't be read: a plugin is never dropped for them.
- **A devpi package index** (the stack's `devpi` service, where `plugin-publisher` puts each test an admin
  installs from the project's catalogue). `DevpiClient.list_versions` lists every version of each package (the
  loader offers whichever a project has); it logs in only when a user and password are both non-empty.
  `DevpiClient.wheel_sha256(package, version)` reads the sha256 of that version's wheel from the index's simple
  page (None when it lists none).

In the AISC stack, a plugin of your own goes in `local_plugins/` of the aisc repository and is installed from a
private catalogue like a public test (see `local_plugins/README.md` there). For how tests are installed and run,
see `docs/architecture.md` ("Running tests") in the aisc repository.

Main calls:

| Call | What it does |
|---|---|
| `list_packages(refresh=False)` | `{package: {version: metadata}}` from both sources |
| `package_meta(package, version)` | the listing's entry of one version (source, module name, local path and provenance) |
| `load_package`, `load_plugin`, `refresh_package` | refused with `CodeNotImported`: plugin code is never imported in the calling process |

`aisc-backend` never imports a plugin: it answers from the manifest stored on the plugin's row, which the worker
reads in its sandbox (`aisc_plugin_interface.manifest`, see `apps/eval`). The worker installs each package with
its dependencies in a fresh environment and runs it as a user of its own.

Modules in `src/aisc_plugin_manager/`: `loader.py` (`Loader`), `devpi_client.py` (`DevpiClient`),
`digest.py` (folder digest and git state).

## Install

Requires Python 3.12 or newer.
Dependencies: `aisc-plugin-interface>=0.5.0`, `httpx`, `packaging` (and `pydantic`, through the
interface). The lock takes the interface from the wheel under `vendor/` (PyPI has no 0.5.0), built by the aisc
repo's `scripts/vendor-plugin-interface.sh`.

The package is not on PyPI. Install it from git:

```bash
uv add git+https://github.com/lux-ai-factory/aisc-plugin-manager --branch pilot
# or the tag of the master branch's release
uv add git+https://github.com/lux-ai-factory/aisc-plugin-manager --tag v0.3.0
```

This branch's `pyproject.toml` says version `0.3.0+aisc.1`. The tag `v0.3.0` (the `master` branch) does not
have the `+local` versions, the digest and git state, the fix that skips the devpi login for empty credentials,
or the 0.5.0 interface. `apps/backend` and `apps/eval` do not install from git: their locks take a
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

The tests resolve `aisc-plugin-interface` 0.5.0 from the wheel under `vendor/` through `uv.lock`;
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
