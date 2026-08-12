# Configuration

## Project `sync.toml`

Keep artifact definitions and per-project locations in the project root:

```toml
version = 1

[git]
remote = "origin"
branch = "main"

[environment]
manager = "uv"
gpu_smoke = ["python", "code/common/environment_smoke.py"]

[artifacts.checkpoints]
path = "checkpoints"
depth = 2

[artifacts.evaluations]
path = "evaluations"
depth = 2

[artifacts.plots]
path = "plots"
depth = 2

[artifacts.references]
path = "references"
depth = 1

[machines.rig-4090]
repo_path = "/absolute/path/on/rig-4090"

[machines.rig-3090-ti]
repo_path = "/absolute/path/on/rig-3090-ti"

[machines.rig-3080-ti]
repo_path = "/absolute/path/on/rig-3080-ti"

[machines.behemoth]
repo_path = "/absolute/path/on/behemoth"
```

`repo_path` must be absolute, and it must sit on that rig's declared `storage_root`. It is
supplied by hand — nothing derives it — so declare every rig the project will ever dispatch to
at once and run `check-paths` before any remote step. Machine keys must match the canonical names
used by dispatch.
`git.remote` names the GitHub remote already configured on the hub; every rig must expose the
same URL under that name. `git.branch` is the branch that dispatch commits are pushed to and that
rigs may fast-forward. Revision deployment stops rather than switching branches or rewriting a
working tree.

`[environment]` is owned by `environment-sync`; it lives here so the same project and machine
selection drive both revision and runtime parity. `rigsync.py` preserves but does not execute it.

## User registry

Keep SSH identity in `~/.config/rigsync/machines.toml`. This file is written **once per user, per
machine**, and every project on that rig reuses it — so it is a prerequisite for scaffolding a
project, not a step inside one. A rig with no entry here cannot be declared in any `sync.toml`:
`load_config` stops rather than inventing an SSH alias or a volume.

```toml
[machines.rig-4090]
ssh = "rig-4090"
hostname = "rig-4090"
storage_root = "/absolute/large-volume/path"

[machines.rig-3090-ti]
ssh = "rig-3090-ti"
hostname = "rig-3090-ti"
storage_root = "/absolute/large-volume/path"

[machines.rig-3080-ti]
ssh = "rig-3080-ti"
hostname = "rig-3080-ti"
storage_root = "/absolute/large-volume/path"

[machines.behemoth]
ssh = "behemoth"
hostname = "behemoth"
storage_root = "/absolute/large-volume/path"
quota_fs = "/dev/filesystem-backing-storage_root"
min_free_gb = 50

[machines.behemoth.caches]
HF_HOME = "/absolute/large-volume/path/cache/huggingface"
UV_CACHE_DIR = "/absolute/large-volume/path/cache/uv"
TMPDIR = "/absolute/large-volume/path/tmp"
```

The `ssh` value is an alias from `~/.ssh/config`; ports, users, and keys remain there. Preserve
legacy aliases that other projects may still reference. When `hostname` is present, `doctor`
compares it exactly with the machine's `hostname` command and stops on a mismatch. Omit the field
only when hostname identity is intentionally unmanaged.

### Storage fields

These live in the **user registry**, not in a project's `sync.toml`, because where a rig keeps its
large volume is a property of the machine and is the same for every project on it. Keeping them
here also keeps machine-specific absolute paths out of committed project files.

`storage_root` is the volume that project checkouts, artifacts, and caches belong on. It is
required on every rig a project dispatches to: `doctor` resolves each project's `repo_path` with
`readlink -f` and stops when the result falls outside the root, and both `doctor` and `check-paths`
fail a rig that declares no root at all rather than reporting a pass they never performed.
Resolving matters: a convenience symlink in `$HOME` can point at the large
volume, so an unresolved string comparison passes while the declaration is still wrong — and stays
wrong the day the symlink is replaced by a real directory.

`quota_fs` names the filesystem to interrogate when the machine enforces **per-user quotas**. It
must be the filesystem **backing `storage_root`** — the volume the work actually lands on — as
reported by `df -P <storage_root>`. Naming a different filesystem produces a headroom number for a
disk nothing is being written to, which reads as reassuring and means nothing.

A quota is not free space, and `df` does not see it: `df` can report terabytes available on a
volume where the user's next write fails with `Disk quota exceeded`. On one rig here the two
differ by 8× on the same mount. When `quota_fs` is present, `doctor` reads the user's allowance
for that filesystem and reports headroom against it; when it is absent, `doctor` falls back to
`df -P <storage_root>`, which is correct on rigs with no quotas.

`min_free_gb` is the hard floor below which `doctor` fails instead of warning. It defaults to a
conservative value; raise it on rigs that run checkpoint-heavy waves. Sizing it is a judgement
about the *wave*, not the rig — a run writing a 350 MB checkpoint every 30 s consumes headroom
far faster than the number suggests.

Omit all three on rigs with a single volume and no quotas; `doctor` then skips the storage check
rather than inventing a default.

### `[machines.<rig>.caches]` — the machine environment

This table is the **single source of truth for a rig's shared caches and scratch**: `HF_HOME`,
`HF_HUB_CACHE`, `HF_DATASETS_CACHE`, `HUGGINGFACE_HUB_CACHE`, `TORCH_HOME`, `UV_CACHE_DIR`,
`TRITON_CACHE_DIR`, `XDG_CACHE_HOME`, `TMPDIR`, `WANDB_CACHE_DIR`, `WANDB_DIR`. That list is
`MACHINE_ENV_VARS` in `scripts/rigsync.py`, which is authoritative; this prose reproduces it and a
test fails when the two drift. Keys must be valid environment names and every value must be an
absolute path. Different rigs mount different volumes, so the values differ per machine while the
variable names do not.

`rigsync provision-env --machines <rigs> --confirm` writes them to `~/.config/rigsync/env.sh` on
each rig and wires that file into the shell startup so **every** shell sees it:

- appended to `~/.zshenv`, which zsh reads on every invocation — interactive or not;
- **prepended** to `~/.bashrc`, above the `case $- in *i*)` guard that returns early for
  non-interactive shells. Appending below that guard is the classic mistake: the exports then
  exist only for a human at a prompt, and are absent for exactly the dispatched, `nohup`'d, and
  `ssh <cmd>` invocations that run the long jobs.

Both edits are idempotent — an existing managed block is stripped before the new one is written —
and each target is backed up once to `<file>.rigsync.bak`. Without `--confirm` the command prints
what it would write and stops, because it modifies shell startup files.

Prefer this over a project's `.env` for anything machine-level, for two reasons. A `.env` is
loaded *after* the shell environment and silently overrides it, so one stale line defeats a
correctly configured rig. And `.env` cannot help the tools that never read it: **`uv` resolves its
cache before Python starts**, so `UV_CACHE_DIR` in a `.env` has no effect at all — which is how a
uv cache quietly becomes the largest directory on a machine.

`doctor` therefore runs two checks once `caches` is declared. It probes a **non-interactive** shell
on the rig and compares each variable against the registry, so a value that only a login shell can
see is reported as drift. And it reads the project's `.env` on that rig and **fails** if it assigns
any machine-level variable. Secrets (`HF_TOKEN`), project-scoped storage, and runtime settings
stay in `.env` and are untouched.

The probe prefers `zsh -c`, because `~/.zshenv` is read by every zsh whether or not anyone is at a
prompt. On a rig with no zsh it falls back to the bare SSH command, which is the equivalent
mechanism: sshd invokes the login shell as the remote shell, and bash reads `~/.bashrc` in exactly
that case — which is what `provision-env`'s prepend targets. A **local** machine with no zsh has no
such substitute, since nothing wraps the call in a shell at all; `doctor` reports the probe as
impossible rather than passing a check it did not make.

Omit `caches` on rigs whose environment is managed some other way; `doctor` then skips both checks
and `.env` remains a legitimate place to set these values.

## Project-scoped storage is repo-relative

Machine-level caches are only half the rule. The other half is that a project's own storage —
its scratch directories, its per-project caches — is declared **relative to the project root**:

```bash
CACHE_DIR=storage/cache
OPENCLIP_CACHE_DIR=storage/openclip
```

`doctor` already requires `repo_path` to resolve under the rig's `storage_root`, so a repo-relative
path lands on the large volume on every rig, automatically and without anyone writing a mount point
down. The consequence is that `.env` contains no rig-specific value at all: the same file is
correct on `rig-4090`, on `behemoth`, and on a rig that does not exist yet, which is what makes
`push-env` a copy rather than a per-rig rendering.

Writing these absolute instead reintroduces the problem the registry solved: the value is right on
the machine it was typed on, names a nonexistent mount on the next, and the natural repair is to
retarget it at `$HOME` — the small volume on a quota'd rig. `doctor` therefore **warns** on any
absolute path in `.env`, machine-level variables aside, and `push-env` repeats the warning before
it copies.

Code reads these through `code/common/paths.py`, which resolves a relative value against the
project root and leaves an absolute one alone.

## Reading the declarations back

Four commands exist so that no path is ever typed twice. Every one of them reads the two files
above and nothing else.

- `check-paths` — offline, no SSH. Confirms each `repo_path` sits on that rig's `storage_root`,
  fails any rig whose registry entry declares no `storage_root`, and lists registry rigs missing
  from `sync.toml`. `doctor` makes the same comparison, but only
  after the repo exists on the rig, so a mistyped path is caught there only once something has
  been cloned into it. Run this **before** `prepare`. Its comparison is lexical; `doctor` still
  resolves both sides with `readlink -f` on the rig, and that division is deliberate — a symlink
  can defeat the lexical check and not the resolved one.
- `repo-path --machine <rig>` — prints that rig's declared project location. `sweep-dispatch`
  substitutes it into the dispatch, monitor, and recovery commands instead of spelling a path
  a second time.
- `storage-env --machine <rig>` — prints `QUOTA_FS`, `MIN_FREE_GB`, and `MIN_FREE_KIB` for a wave
  script's pre-flight guard. A wave may raise the floor for its own checkpoint footprint; it must
  not sink below the registry's.
- `push-env --machine <rig> --dry-run|--confirm` — copies the hub's `.env` to a peer. `.env` is
  ignored by Git, so a freshly `prepare`d peer has none; nothing else puts one there. It is a
  protected write that **carries secrets**, it refuses a `.env` that assigns machine-level
  variables, and it will not overwrite a peer's existing file without `--overwrite`.

## Environment overrides

- `RIGSYNC_CONFIG` changes the project config path; default: `./sync.toml`.
- `RIGSYNC_REGISTRY` changes the user registry path; default:
  `~/.config/rigsync/machines.toml`.
