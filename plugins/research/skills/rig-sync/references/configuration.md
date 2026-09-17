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

`repo_path` must be absolute, and it must sit under one of that rig's declared storage roots. It is
supplied by hand — nothing derives it — so declare every rig the project will ever dispatch to
at once and run `check-paths` before any remote step. Machine keys must match the canonical names
used by dispatch.
`git.remote` names the GitHub remote already configured on the hub; every rig must expose the
same URL under that name. `git.branch` is the branch that dispatch commits are pushed to and that
must contain every deployed wave commit. Revision deployment never moves a main checkout: it adds
the wave worktree `<repo_path>/.waves/<wave_id>`, and stops rather than switching branches or
rewriting a working tree. Keyed wave environments live beside it in `<repo_path>/.envs/`. The
project `.gitignore` must ignore both directories.

`[environment]` is owned by `environment-sync`; it lives here so the same project and machine
selection drive both revision and runtime parity. `rigsync.py` preserves but does not execute it.

## User registry

Keep SSH identity in `~/.config/rigsync/machines.toml`. This file is written **once per user, per
machine**, and every project on that rig reuses it — so it is a prerequisite for scaffolding a
project, not a step inside one. A rig with no entry here cannot be declared in any `sync.toml`:
`load_config` stops rather than inventing an SSH alias or a volume. The same file carries the
fleet board declaration, a top-level `[board]` table read only by `rig-board` (`root`, `rigs`,
`shared`, `port`, `bind`; see that skill's `references/configuration.md`); rigsync ignores it.

```toml
[machines.rig-4090]
ssh = "rig-4090"
hostname = "rig-4090"
storage_roots = ["/absolute/large-volume/path", "/absolute/second-volume/path"]

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
storage_roots = [
  { path = "/absolute/large-volume/path", quota_fs = "/dev/filesystem-backing-that-path", min_free_gb = 50 },
]

[machines.behemoth.caches]
HF_HOME = "/absolute/large-volume/path/cache/huggingface"
UV_CACHE_DIR = "/absolute/large-volume/path/cache/uv"
TMPDIR = "/absolute/large-volume/path/tmp"
```

A Slurm cluster such as `leonardo` is declared the same way, with two deliberate omissions and
two additions. Omitted: no `hostname`, because the alias lands on whichever login node is free,
and no `quota_fs`, because `quota -w` prints nothing on its Lustre filesystems while
`df -P <root>` already reports the project quota, which is what the fallback measures.
Added: `gpus = "job"` and `transfer_ssh` (see [Cluster fields](#cluster-fields)). Its single
storage root is the project's absolute `$WORK` path, one entry per project account. The exact
entry is in `sweep-dispatch/references/cineca-slurm.md`.

The `ssh` value is an alias from `~/.ssh/config`; ports, users, and keys remain there. Preserve
legacy aliases that other projects may still reference. When `hostname` is present, `doctor`
compares it exactly with the machine's `hostname` command and stops on a mismatch. Omit the field
only when hostname identity is intentionally unmanaged.

### Storage fields

These live in the **user registry**, not in a project's `sync.toml`, because where a rig keeps its
large volume is a property of the machine and is the same for every project on it. Keeping them
here also keeps machine-specific absolute paths out of committed project files.

A **storage root** is a volume that project checkouts, artifacts, and caches belong on. A machine
may have several — a workstation with more than one data disk, say — and each project lives on
exactly one of them. At least one root is required on every rig a project dispatches to.

- `storage_roots` lists them. Each entry is an absolute path, or a table
  `{ path, quota_fs, min_free_gb, system_disk }` carrying that volume's own rules, since disks on
  one machine differ: one may be quota'd, another may need a larger floor.
- `storage_root = "..."` is the single-root form, still accepted. There, machine-level `quota_fs`
  and `min_free_gb` are that root's rules. Declaring both forms is an error.
- With `storage_roots`, a machine-level `min_free_gb` is the default floor for every root. A
  machine-level `quota_fs` is refused: it cannot say which root it backs.
- Roots must not repeat or nest, so a path belongs to at most one of them.

A project never names its root. Its root is **the declared root containing its resolved
`repo_path`**, so placing a checkout on a volume is the whole declaration. `doctor` resolves
`repo_path` and every root with `readlink -f`, stops when the project sits under none of them, and
names the root it found. `check-paths` makes the same choice lexically. Both fail a rig that
declares no root at all rather than reporting a pass they never performed. Resolving matters: a
convenience symlink in `$HOME` can point at the large volume, so an unresolved string comparison
passes while the declaration is still wrong — and stays wrong the day the symlink is replaced by a
real directory.

`doctor` also fails a root whose filesystem is the one mounted at `/`. An unmounted disk leaves
its mount point behind as an empty directory on the system disk, where every path check still
passes and the work silently fills the wrong drive. Set `system_disk = true` on a root only when it
genuinely lives on the system filesystem.

`quota_fs` names the filesystem to interrogate when a root is bounded by **per-user quotas**. It
must be the filesystem **backing that root** — the volume the work actually lands on — as
reported by `df -P <root>`, and `doctor` fails when the two disagree. Naming a different
filesystem produces a headroom number for a disk nothing is being written to, which reads as
reassuring and means nothing.

A quota is not free space, and `df` does not see it: `df` can report terabytes available on a
volume where the user's next write fails with `Disk quota exceeded`. On one rig here the two
differ by 8× on the same mount. When the project's root declares `quota_fs`, `doctor` reads the
user's allowance for that filesystem and reports headroom against it; otherwise it uses
`df -P <root>`, which is correct on volumes with no quotas, and on a cluster work area whose `df`
already reports the project quota.

`min_free_gb` is the hard floor below which `doctor` fails instead of warning. It defaults to a
conservative value; raise it on rigs that run checkpoint-heavy waves. Sizing it is a judgement
about the *wave*, not the rig — a run writing a 350 MB checkpoint every 30 s consumes headroom
far faster than the number suggests.

Omit all three on rigs with a single volume and no quotas; `doctor` then skips the storage check
rather than inventing a default.

### Cluster fields

Two optional fields describe a machine whose ssh alias is a **Slurm login node** rather than the
box the GPUs are in. Omit both on every ordinary rig; nothing changes there.

`gpus = "job"` declares that the ssh target has no GPU and that GPUs exist only inside scheduled
jobs. The default, `"ssh"`, is the rig case: `nvidia-smi` is reachable over the alias. `rigsync`
only records the flag; `environment-sync` acts on it. With `gpus = "job"`, its `doctor` and
`verify` skip the `nvidia-smi` probe, still compare OS, architecture, and libc with the hub and
still require the environment fingerprint to match, report `gpus=deferred to job`, and **refuse
`--lane` for that machine** with a message saying so — the GPU smoke runs as the first step inside
every job instead. `provision --confirm` therefore completes on a login node instead of installing
correctly and then failing its own final verify.

`transfer_ssh` names a second alias from `~/.ssh/config` that reaches the **same filesystem** as
`ssh` — a cluster's data mover. It is used by `pull` and `push` **only**, i.e. by artifact rsync:
`doctor`, `check-paths`, `push-source`, revision deployment, `provision-env`, and every probe keep
using `ssh`, and so does the `mkdir -p` that `push` runs before its rsync. rsync paths are already
absolute, so a data mover with no `$WORK` or login environment is fine. Declare it on clusters
whose login nodes kill long processes: a multi-hundred-GB pull through the login node dies at the
CPU-time limit, and the skill forbids a hand-written rsync as the workaround. `pull`/`push` print
`via <transfer_ssh>` and the exact rsync command line, so the alias in use is always visible.

Whether or not `transfer_ssh` is declared, `pull` and `push` run rsync with `--partial` and
**retry a bounded number of times** (three attempts) when rsync exits 12 (protocol data stream,
what a killed remote produces) or 255 (ssh died), printing a `[retry]` note each time. rsync is
idempotent, so a retry resumes from what already landed. Any other exit code is a real transfer
error and is not retried.

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

`doctor` already requires `repo_path` to resolve under one of the rig's storage roots, so a
repo-relative path lands on the project's large volume on every rig, automatically and without anyone writing a mount point
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

- `check-paths` — offline, no SSH. Confirms each `repo_path` sits under one of that rig's storage
  roots and names it, fails any rig whose registry entry declares no root, and lists registry rigs missing
  from `sync.toml`. `doctor` makes the same comparison, but only
  after the repo exists on the rig, so a mistyped path is caught there only once something has
  been cloned into it. Run this **before** `prepare`. Its comparison is lexical; `doctor` still
  resolves both sides with `readlink -f` on the rig, and that division is deliberate — a symlink
  can defeat the lexical check and not the resolved one.
- `repo-path --machine <rig>` — prints that rig's declared project location. `sweep-dispatch`
  substitutes it into the dispatch, monitor, and recovery commands instead of spelling a path
  a second time.
- `storage-env --machine <rig>` — prints `QUOTA_FS`, `MIN_FREE_GB`, and `MIN_FREE_KIB` of the
  project's storage root for a wave script's pre-flight guard. With several roots it resolves
  `repo_path` on the rig to pick that root, and fails when none contains it. A wave may raise the floor for its own checkpoint footprint; it must
  not sink below the registry's.
- `push-env --machine <rig> --dry-run|--confirm` — copies the hub's `.env` to a peer. `.env` is
  ignored by Git, so a freshly `prepare`d peer has none; nothing else puts one there. It is a
  protected write that **carries secrets**, it refuses a `.env` that assigns machine-level
  variables, and it will not overwrite a peer's existing file without `--overwrite`.

## Environment overrides

- `RIGSYNC_CONFIG` changes the project config path; default: `./sync.toml`.
- `RIGSYNC_REGISTRY` changes the user registry path; default:
  `~/.config/rigsync/machines.toml`.
