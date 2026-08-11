---
name: environment-sync
description: Provision and verify one exact uv-managed Python environment across configured Research 2.0 rigs. Use when installing or repairing a supported project environment, diagnosing Python/package drift, validating GPU runtime compatibility, or establishing the mandatory parity gate before dispatch. Do not use to adopt legacy projects, converge operating systems, manage containers or Conda, mutate system packages, or write outside an approved engineering envelope.
---

# environment-sync

Use `scripts/envsync.py` from a structured research-project root. Read
[references/configuration.md](references/configuration.md) when establishing or repairing the
project contract. Copy `assets/environment.py` to `code/common/environment.py`; keep that tracked
helper identical across the deployed Git revision.

Read `program/00-execution-agreement.md` and the Research 2.0 conventions first. Stop when the
project surfaces are absent. In engineering-manual mode, preview and wait before `--confirm`; in
engineering-auto mode, confirm only for the exact revision, rigs, and environment mutation covered
by the approved envelope. Missing tools, new destinations, credentials, and scope expansion remain
protected.

## Contract

- Require committed `pyproject.toml`, `uv.lock`, `.python-version`, `sync.toml`, and
  `code/common/environment.py`.
- Require an exact `X.Y.Z` Python pin, exact `[tool.uv].required-version = "==X.Y.Z"`, and
  `[environment].manager = "uv"`, a user-chosen single-directory `name`, and a tokenized Python
  `gpu_smoke` command. Before creating an environment whose name has not been chosen, ask the user
  which name the virtual environment should have; suggest `.venv` only as an option and never infer
  or silently default the name.
  Reject names that collide with the project taxonomy, `.git`, `.env`, or `.rigsync_cache`.
- Treat the invariant fingerprint as the lock digest, exact Python implementation/version, and
  canonical installed distribution names/versions. Report OS, architecture, libc, GPU, and
  driver facts separately; allow host differences only when the project GPU smoke passes.
- Never copy the configured environment directory, sync system packages, use `sudo`, modify shell
  profiles, or silently convert requirements/Conda projects. Treat those layouts as unsupported
  rather than converting them.

## Workflow

1. Run `doctor` read-only on the intended rigs. Stop on missing contract files, incompatible
   OS/architecture, missing Git/SSH/curl/GPU tooling, or ambiguous machine identity.
2. Run `provision --dry-run` for the exact revision and named rigs. Show pinned uv/Python, every
   target, and each available `uv sync --frozen --exact --dry-run` result; when pinned uv is
   absent, show its versioned installer plan. Authorize `--confirm` according to engineering mode.
3. On confirmation, install pinned uv under ignored `.rigsync_cache/`, install the exact
   uv-managed Python, and exact-sync the configured named environment. Refuse mutation while the
   project has active tmux lanes or `running` statuses.
4. Run `verify` across the complete rig set, then once per approved GPU lane. Require every rig's
   fingerprint to equal the local hub and every bounded GPU smoke to succeed.
5. Give the verified fingerprint to `sweep-dispatch`; embed it in wave scripts and status
   provenance. Re-run verification before initial launch and recovery.

## Commands

```bash
ENVSYNC_SCRIPT=<installed environment-sync>/scripts/envsync.py
python3 "$ENVSYNC_SCRIPT" doctor --machines rig-4090,rig-3090-ti,behemoth
python3 "$ENVSYNC_SCRIPT" provision --revision <40-char-sha> \
  --machines rig-4090,rig-3090-ti --dry-run
python3 "$ENVSYNC_SCRIPT" provision --revision <40-char-sha> \
  --machines rig-4090,rig-3090-ti --confirm
python3 "$ENVSYNC_SCRIPT" verify --revision <40-char-sha> \
  --machines rig-4090,rig-3090-ti
python3 "$ENVSYNC_SCRIPT" verify --revision <40-char-sha> \
  --lane rig-4090:0 --lane rig-3090-ti:0
```

`verify` is read-only. `provision` fails closed on partial fleet success; do not dispatch until a
subsequent full-set verification passes. Treat environment exit `87` in a wave as pre-execution
drift/incompatibility, not an experiment failure.

## Safety and stop conditions

- Reuse project `sync.toml` and the user rig registry; never infer aliases, paths, credentials,
  private indexes, or extra `behemoth` GPU authorization.
- Use only the official versioned uv installer, with no profile changes, after named-target
  authorization under the active engineering mode. Never log index credentials or values from `.env`.
- Keep uv's default dependency-group selection authoritative. Reject per-rig extras, manual
  installs, missing locks, stale locks, and non-exact pins.
- If any rig fails installation, fingerprint comparison, or GPU smoke, report the exact rig and
  check and stop. Do not roll back by deleting environments or repair one rig during launch.
