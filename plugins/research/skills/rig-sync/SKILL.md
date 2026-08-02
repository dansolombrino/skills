---
name: rig-sync
description: Synchronize research-project source and artifacts across configured GPU rigs with additive rsync-over-SSH transfers. Use when Codex needs to prepare a project for remote dispatch, verify cross-rig paths and dependencies, see which rig holds checkpoints or evaluations, push the current non-ignored worktree, or pull/push selected artifacts. Do not use for continuous mirroring, deletion, or unapproved remote writes.
---

# rig-sync

Use the bundled `scripts/rigsync.py` from a structured research-project root. Read
[references/configuration.md](references/configuration.md) when creating or repairing `sync.toml`
or the user registry.

## Safety contract

- Run `doctor` before dispatch and `status` before artifact movement.
- Show a `--dry-run` before every remote write, then get user approval for the named source,
  destination, and selector. An already-approved sweep authorizes syncing only that sweep's
  project source to its assigned rig paths.
- Never add `--delete`, delete remote files, or infer an SSH alias/path.
- Treat `rig-4090` as local when its configured name or hostname matches the current host; do not
  require self-SSH. Use bounded, noninteractive SSH for peers.
- Treat a configured registry `hostname` as an identity assertion: `doctor` must compare it with
  the selected machine's observed hostname and fail on drift.
- Stop dispatch when `doctor` fails for an assigned rig.

## Commands

Set `RIGSYNC_SCRIPT` to this skill's `scripts/rigsync.py`, then run:

```bash
python3 "$RIGSYNC_SCRIPT" doctor --machines rig-4090,rig-3090-ti,behemoth
python3 "$RIGSYNC_SCRIPT" prepare --machine rig-3090-ti --dry-run
python3 "$RIGSYNC_SCRIPT" prepare --machine rig-3090-ti --confirm
python3 "$RIGSYNC_SCRIPT" status --group evaluations
python3 "$RIGSYNC_SCRIPT" push-source --to rig-3090-ti --dry-run
python3 "$RIGSYNC_SCRIPT" push-source --to rig-3090-ti --confirm
python3 "$RIGSYNC_SCRIPT" pull evaluations/000_exp --from rig-3090-ti --dry-run
python3 "$RIGSYNC_SCRIPT" pull evaluations/000_exp --from rig-3090-ti --confirm
```

`push-source` transfers Git-tracked files plus non-ignored untracked files. It excludes `.git`,
ignored secrets, environments, logs, and artifact contents according to the project's
`.gitignore`. It is the source-staging gate used by `$sweep-dispatch`.

Artifact selectors are `<group>` or `<group>/<relative/path>`, where `<group>` is declared under
`[artifacts]` in `sync.toml`. `pull` means peer → current rig; `push` means current rig → peer.

## Setup and stop conditions

- Require project-local `sync.toml` and user-local `~/.config/rigsync/machines.toml`. For a new
  destination, preview and approve `prepare` before requiring a green `doctor`.
- Preserve existing registry entries. Add canonical rig aliases only from discovered SSH config
  and hostname facts or explicit user input.
- Keep project locations only in `sync.toml`; keep SSH identity only in the user registry.
- If `rsync`, `ssh`, the project path, key authentication, or a machine mapping is missing, stop
  with the exact failed check. Never substitute `scp`, ad-hoc tarballs, or hand-written rsync.
