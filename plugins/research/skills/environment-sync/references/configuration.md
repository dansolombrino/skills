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
name = ".venv"
gpu_smoke = ["python", "code/common/environment_smoke.py"]
```

`name` is the hub **development** environment: the user's interpreter for the IDE, ad-hoc runs,
and `uv add`. Waves never use it. Before adding `name`, ask the user which single directory name
to use. `.venv` may be suggested, but never inferred or silently selected. Reject paths, `.` and
`..`; also reject names that collide with the project taxonomy, `.git`, `.env`, `.envs`, `.waves`,
or `.rigsync_cache`. The chosen name is committed.

Wave environments are not named: they live at `<repo_path>/.envs/<env_key>`, where `env_key` is
the first 16 hex digits of a SHA-256 over the wave revision's `uv.lock`, `.python-version`, and
`[tool.uv].required-version`. `environment-sync` builds each one from the wave worktree with
`uv sync --frozen --exact --no-install-project`, so a single environment serves every worktree with
the same inputs. The project `.gitignore` ignores `.envs/` and `.waves/`.

`gpu_smoke` is an argv array, not shell, of the form `["python", "<script>", ...args]`. Its first
token must be `python`; environment sync replaces that token with the wave environment's
`bin/python` and supplies `CUDA_VISIBLE_DEVICES` for the approved lane. The second token is the
smoke script, written **relative to the project root** and resolved against each rig's own
wave worktree (`<repo_path>/.waves/<wave_id>`) — the verification runs over SSH with no working
directory, so a path left unresolved would be read from `$HOME` on every peer. An option in that slot, an absolute path, or one
escaping the project with `..` is rejected when the config loads. Any further tokens are passed to
the script unchanged. Keep the test quick and bounded, perform a real device operation through the
project's locked framework, and exit nonzero on incompatibility. Do not encode GPU ownership in
this command; `sweep-dispatch` owns per-wave authorization.

Copy `assets/environment.py` from the environment-sync skill's own installed directory to
`code/common/environment.py`. Generated wave scripts
invoke the worktree's copy, `fingerprint --lock <worktree>/uv.lock`, before experiment Python.

## Machine-local state

- `.envs/<env_key>/` — exact wave environments, ignored, never copied, removed only by an approved
  `rig-sync prune`.
- `.waves/<wave_id>/` — wave worktrees created by `rig-sync`, ignored.
- `<environment.name>/` — the hub development environment, user-managed, ignored and never copied.
- `.rigsync_cache/tools/uv/<version>/` — exact standalone uv binary, ignored.
- uv-managed Python and package/download caches — user-local, machine-local, never fingerprinted.
- `.env`, credentials, datasets, drivers, CUDA devices, logs, and artifacts — outside the
  invariant environment identity.

Machine-local does not mean unmanaged. *Where* these caches live is declared per rig in
`[machines.<rig>.caches]` (`rig-sync` → `references/configuration.md`) and verified by `doctor`;
only their *contents* stay outside environment identity. `UV_CACHE_DIR` in particular cannot be
set from a project `.env` — uv resolves its cache before Python starts — so it has to come from
the rig's machine environment, which `rigsync provision-env` installs. Left on its default path,
a uv cache is routinely one of the largest directories on a research rig.

`.env` is the other half of that split, and it holds no absolute path at all: project-scoped
storage is declared relative to the project root and resolved by `code/common/paths.py`. Since
`doctor` already requires each rig's `repo_path` to sit on that rig's `storage_root`, relative
paths land on the large volume everywhere without the file naming a mount point. That is what
makes `.env` rig-independent and copyable to a peer with `rigsync push-env`.

Environment sync requires Linux machines with the same architecture. libc, kernel, driver, and
GPU model may differ; their compatibility is established by successful creation of the locked
environment plus the project GPU smoke.

A machine whose registry entry declares `gpus = "job"` (a Slurm login node, see
`rig-sync/references/configuration.md`, "Cluster fields") has no GPU on the ssh target. `doctor`
and `verify` skip the `nvidia-smi` probe there and print `gpus=deferred to job`; OS, architecture,
libc, uv version, lock, and fingerprint are checked exactly as on a rig. `verify --lane` refuses
such a machine, because the smoke can only run inside a job — which is where the dispatch
templates already run it as the first step.

## Adoption

For an existing project, inventory its interpreter, direct/transitive packages, custom indexes,
editable/VCS dependencies, and current smoke command. Propose the resulting pyproject, exact pins,
and lock to the user. Test the uv environment on the hub before replacing or modifying any peer
environment. Never translate Conda channels, system CUDA packages, or private indexes by guess.
