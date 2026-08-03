# Environment contract

## Project files

Commit an exact Python pin:

```text
3.12.11
```

Configure the project and pin the uv implementation used to materialize it:

```toml
[project]
name = "example-project"
version = "0.1.0"
requires-python = "==3.12.*"
dependencies = []

[tool.uv]
required-version = "==0.11.32"
python-preference = "managed"
```

`uv.lock` is the only dependency resolution accepted on peers. Select runtime dependencies and
default dependency groups in `pyproject.toml`; do not pass per-rig extras to environment sync.

Extend the existing project `sync.toml`:

```toml
[environment]
manager = "uv"
gpu_smoke = ["python", "code/common/environment_smoke.py"]
```

`gpu_smoke` is an argv array, not shell. Its first token must be `python`; environment sync
replaces that token with the project's `.venv/bin/python` and supplies `CUDA_VISIBLE_DEVICES` for
the approved lane. Keep the test quick and bounded, perform a real device operation through the
project's locked framework, and exit nonzero on incompatibility. Do not encode GPU ownership in
this command; `$sweep-dispatch` owns per-wave authorization.

Copy the skill's `assets/environment.py` to `code/common/environment.py`. Generated wave scripts
invoke its `fingerprint --lock uv.lock` command before experiment Python.

## Machine-local state

- `.venv/` — exact project environment, ignored and never copied.
- `.rigsync_cache/tools/uv/<version>/` — exact standalone uv binary, ignored.
- uv-managed Python and package/download caches — user-local, machine-local, never fingerprinted.
- `.env`, credentials, datasets, drivers, CUDA devices, logs, and artifacts — outside the
  invariant environment identity.

Environment sync requires Linux machines with the same architecture. libc, kernel, driver, and
GPU model may differ; their compatibility is established by successful creation of the locked
environment plus the project GPU smoke.

## Adoption

For an existing project, inventory its interpreter, direct/transitive packages, custom indexes,
editable/VCS dependencies, and current smoke command. Propose the resulting pyproject, exact pins,
and lock to the user. Test the uv environment on the hub before replacing or modifying any peer
environment. Never translate Conda channels, system CUDA packages, or private indexes by guess.
