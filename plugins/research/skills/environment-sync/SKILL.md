---
name: environment-sync
description: Provision and verify exact, lock-keyed uv-managed Python wave environments across configured Research 2.0 rigs. Use when installing or repairing a supported project environment, building the environment for a wave or for the staged lock before the pre-dispatch smoke, diagnosing Python/package drift, validating GPU runtime compatibility, or establishing the mandatory parity gate before dispatch. Do not use to adopt legacy projects, converge operating systems, manage containers or Conda, or mutate system packages.
---

# environment-sync

Run this skill's bundled `scripts/envsync.py` against a structured research-project root. Read
[references/configuration.md](references/configuration.md) when establishing or repairing the
project contract. Copy this skill's `assets/environment.py` to `code/common/environment.py`; keep
that tracked helper identical across the deployed Git revision.

Two kinds of environment exist, and this skill builds only the second:

- **Hub development environment** — the directory named by `[environment].name` in the hub's main
  checkout. It is the user's: IDE interpreter, ad-hoc runs, `uv add`. No wave, smoke, or dispatch
  command uses it, and this skill never modifies it.
- **Wave environments** — `<repo_path>/.envs/<env_key>/` on every machine, where `env_key` hashes
  the wave revision's `uv.lock`, `.python-version`, and uv pin. Waves with the same inputs share
  one; a changed lock gets a new one. Each is built from the wave worktree `.waves/<wave_id>`
  (created by `rig-sync deploy-revision`) without installing the project itself, and is never
  re-synced while an active wave uses it.

**Resolving this skill's own files.** `scripts/` and `assets/` here mean *this skill's installed
directory* — the folder holding the SKILL.md you are reading — not the research project, whose
`scripts/` holds shell wave scripts only. `scripts/envsync.py` imports `rig-sync`'s
`scripts/rigsync.py` by sibling path, so both skills must be installed from the same plugin.

Project facts: `../research-project-init/references/conventions.md`. A new environment key is
additive and never touches one an active wave uses.

## Contract

- Require committed `pyproject.toml`, `uv.lock`, `.python-version`, `sync.toml`, and
  `code/common/environment.py`.
- Require an exact `X.Y.Z` Python pin, exact `[tool.uv].required-version = "==X.Y.Z"`, and
  `[environment].manager = "uv"`, a single-directory `name` for the hub development
  environment (`.venv` unless the user names another), and a tokenized Python `gpu_smoke`
  command.
  Reject names that collide with the project taxonomy, `.git`, `.env`, `.envs`, `.waves`, or
  `.rigsync_cache`.
- Require the project `.gitignore` to ignore `.waves/` and `.envs/`, and the project not to be
  installed into its environment (no build system, or `--no-install-project` semantics, which
  this skill always passes).
- Treat the invariant fingerprint as the lock digest, exact Python implementation/version, and
  canonical installed distribution names/versions. Report OS, architecture, libc, GPU, and
  driver facts separately; allow host differences only when the project GPU smoke passes.
- Never copy an environment directory, sync system packages, use `sudo`, modify shell
  profiles, or silently convert requirements/Conda projects. Treat those layouts as unsupported
  rather than converting them.

## Workflow

1. Run `doctor` read-only on the intended rigs. Stop on missing contract files, incompatible
   OS/architecture, missing Git/SSH/curl/GPU tooling, or ambiguous machine identity. This doctor
   runs `rig-sync`'s first, so it also stops on the storage conditions described there: a
   `repo_path` resolving outside every declared storage root of the rig, a root on the system
   filesystem, insufficient headroom against the quota, machine cache variables a **non-interactive** shell cannot see, and a project `.env`
   that re-declares them. Provisioning onto a rig whose storage is misdeclared installs a
   multi-gigabyte environment on the wrong volume.
2. Run `provision --dry-run` for the exact target and named rigs: `--wave <wave_id> --revision <sha>`
   after `rig-sync deploy-revision` created the wave worktrees (always including the hub), or
   `--staged --machines <hub>` before the dispatch commit exists, for the pre-dispatch smoke (the
   hub's environment files must then equal the index). It shows pinned uv/Python, the environment
   key and path, every target, and each `uv sync --frozen --exact --no-install-project --dry-run`
   result (or the versioned uv installer plan). Then `--confirm`.
3. On confirmation, install pinned uv under ignored `.rigsync_cache/`, install the exact
   uv-managed Python, and exact-sync `.envs/<env_key>`. Building a key that does not exist yet is
   always allowed, even while other waves run. Re-syncing an existing key is refused while any
   active wave on that machine uses it, or while activity cannot be attributed to a wave.
4. Run `verify` with the same target across the complete rig set, then once per pool GPU lane.
   Require every rig's fingerprint to equal the hub's and every bounded GPU smoke to succeed. A machine whose
   registry entry declares `gpus = "job"` (a Slurm login node) is verified with `--machines`
   only: the GPU probe is skipped and reported as `gpus=deferred to job`, `--lane` is refused for
   it, and the smoke runs as the first step of every job instead.
5. Give the printed `ENVIRONMENT_DIR` and fingerprint to `sweep-dispatch`; embed both in wave
   scripts and the fingerprint in status provenance. Re-run verification before initial launch and
   recovery. Unused keys are removed only by `rig-sync prune`, after its dry run.

## Commands

```bash
# Absolute path to this skill's own scripts/envsync.py; see "Resolving this skill's own files".
ENVSYNC_SCRIPT=/absolute/path/to/environment-sync/scripts/envsync.py
python3 "$ENVSYNC_SCRIPT" doctor --machines rig-4090,rig-3090-ti,behemoth
python3 "$ENVSYNC_SCRIPT" provision --staged --machines rig-4090 --dry-run
python3 "$ENVSYNC_SCRIPT" verify --staged --machines rig-4090
python3 "$ENVSYNC_SCRIPT" provision --wave 20260802-120000 --revision <40-char-sha> \
  --machines rig-4090,rig-3090-ti --dry-run
python3 "$ENVSYNC_SCRIPT" provision --wave 20260802-120000 --revision <40-char-sha> \
  --machines rig-4090,rig-3090-ti --confirm
python3 "$ENVSYNC_SCRIPT" verify --wave 20260802-120000 --revision <40-char-sha> \
  --machines rig-4090,rig-3090-ti
python3 "$ENVSYNC_SCRIPT" verify --wave 20260802-120000 --revision <40-char-sha> \
  --lane rig-4090:0 --lane rig-3090-ti:0
```

`verify` is read-only and ends with `ENVIRONMENT_KEY=`, `ENVIRONMENT_DIR=`, and
`ENVIRONMENT_FINGERPRINT=` lines. `provision` fails closed on partial fleet success; do not dispatch until a
subsequent full-set verification passes. Treat environment exit `87` in a wave as pre-execution
drift/incompatibility, not an experiment failure.

## Safety and stop conditions

- Reuse project `sync.toml` and the user rig registry; never infer aliases, paths, credentials,
  private indexes, or extra `behemoth` GPU authorization.
- Use only the official versioned uv installer, with no profile changes, on the named targets.
  Never log index credentials or values from `.env`.
- Keep uv's default dependency-group selection authoritative. Reject per-rig extras, manual
  installs, missing locks, stale locks, and non-exact pins.
- If any rig fails installation, fingerprint comparison, or GPU smoke, report the exact rig and
  check and stop. Do not roll back by deleting environments or repair one rig during launch.
