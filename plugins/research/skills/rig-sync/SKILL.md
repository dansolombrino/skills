---
name: rig-sync
description: Deploy exact tested Git revisions and synchronize selected research artifacts across configured GPU rigs. Use when Codex needs to prepare a rig clone, verify GitHub access and rig identity, fast-forward machines to an approved wave commit, check cross-rig revision consistency, or inspect and transfer selected artifacts. Do not use for continuous mirroring, destructive Git recovery, deletion, or unapproved remote writes.
---

# rig-sync

Use the bundled `scripts/rigsync.py` from a structured research-project root. Read
[references/configuration.md](references/configuration.md) when creating or repairing `sync.toml`
or the user registry. Git/GitHub distributes launch source; rsync moves selected artifacts.

## Safety contract

- Run `doctor` before dispatch, `verify-revision` before every initial/recovery launch, and
  `status` before artifact movement.
- Show a `--dry-run` before every remote write, then get user approval for the named source,
  destination, and selector. An approved assignment may explicitly authorize its commit, tag,
  push, and fast-forward deployment to the named rigs.
- Never force-push, reset, stash, clean, merge non-fast-forward, add `--delete`, delete remote
  files, or infer an SSH alias/path.
- Treat `rig-4090` as local when its configured name or hostname matches the current host; do not
  require self-SSH. Use bounded, noninteractive SSH for peers.
- Treat a configured registry `hostname` as an identity assertion: `doctor` must compare it with
  the selected machine's observed hostname and fail on drift.
- Stop dispatch when `doctor` fails for an assigned rig.
- Refuse revision changes when execution paths are dirty, the branch/remote differs, another
  revision has active tmux lanes or `running` statuses, or the repository uses unconfigured
  submodules/Git LFS.

## Commands

Set `RIGSYNC_SCRIPT` to this skill's `scripts/rigsync.py`, then run:

```bash
python3 "$RIGSYNC_SCRIPT" doctor --machines rig-4090,rig-3090-ti,behemoth
python3 "$RIGSYNC_SCRIPT" prepare --machine rig-3090-ti --dry-run
python3 "$RIGSYNC_SCRIPT" prepare --machine rig-3090-ti --confirm
python3 "$RIGSYNC_SCRIPT" deploy-revision --wave 20260802-120000 \
  --revision <40-char-sha> --branch main --machines rig-4090,rig-3090-ti --dry-run
python3 "$RIGSYNC_SCRIPT" deploy-revision --wave 20260802-120000 \
  --revision <40-char-sha> --branch main --machines rig-4090,rig-3090-ti --confirm
python3 "$RIGSYNC_SCRIPT" verify-revision --wave 20260802-120000 \
  --revision <40-char-sha> --branch main --machines rig-4090,rig-3090-ti
python3 "$RIGSYNC_SCRIPT" status --group evaluations
python3 "$RIGSYNC_SCRIPT" pull evaluations/000_exp --from rig-3090-ti --dry-run
python3 "$RIGSYNC_SCRIPT" pull evaluations/000_exp --from rig-3090-ti --confirm
```

`deploy-revision` fetches the configured branch and annotated `wave--<wave_id>` tag, requires both
to resolve to the approved SHA, and advances only with `git merge --ff-only`. It verifies exact
`HEAD`, branch, remote URL, tag, and clean `code/`, `config/`, `scripts/`, and dependency manifests
afterward. `$sweep-dispatch` uses this gate; `push-source` remains available only for legacy or
non-launch staging and never establishes launch consistency.

Artifact selectors are `<group>` or `<group>/<relative/path>`, where `<group>` is declared under
`[artifacts]` in `sync.toml`. `pull` means peer → current rig; `push` means current rig → peer.

## Setup and stop conditions

- Require project-local `sync.toml` and user-local `~/.config/rigsync/machines.toml`. For a new
  destination, preview and approve `prepare`; with `[git]` configured it clones the hub remote
  into an absent/empty path and refuses a non-empty non-Git directory.
- Preserve existing registry entries. Add canonical rig aliases only from discovered SSH config
  and hostname facts or explicit user input.
- Keep project locations only in `sync.toml`; keep SSH identity only in the user registry.
- If Git/rsync/SSH, the project path, GitHub authentication, remote identity, or a machine mapping
  is missing, stop with the exact failed check. Never substitute `scp`, ad-hoc tarballs, or
  hand-written rsync.
