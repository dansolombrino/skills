---
name: rig-sync
description: Deploy exact tested Git revisions and synchronize selected artifacts across configured Research 2.0 GPU rigs. Use when preparing an approved empty rig clone, verifying GitHub and rig identity, fast-forwarding machines to an authorized wave commit, checking cross-rig revision consistency, or transferring selected artifacts. Pair with environment-sync; do not use for legacy projects, environment provisioning, continuous mirroring, destructive Git recovery, deletion, or writes outside an approved engineering envelope.
---

# rig-sync

Run this skill's bundled `scripts/rigsync.py` against a structured research-project root. Read
[references/configuration.md](references/configuration.md) when creating or repairing `sync.toml`
or the user registry. Git/GitHub distributes launch source; rsync moves selected artifacts.

**Resolving this skill's own files.** `scripts/rigsync.py` means *this skill's installed
directory* — the folder holding the SKILL.md you are reading — not the research project. Use that
directory's absolute path. Never resolve it against the project root: the project's own `scripts/`
holds shell wave scripts only and has no `rigsync.py`.

Read `program/00-execution-agreement.md` and the Research 2.0 conventions
(`../research-project-init/references/conventions.md`, including "Directives are closed" — never
infer paths, machine entries, or defaults from another project on disk) first. Stop when the
project surfaces are absent. In engineering-manual mode, show the dry run and wait; in
engineering-auto mode, confirm only the named source, destination, revision, and selector covered
by the approved envelope. New destinations and any destructive recovery remain protected.

## Safety contract

- Run `doctor` before dispatch, `verify-revision` before every initial/recovery launch, and
  `status` before artifact movement. Run `environment-sync verify` separately after revision
  deployment; Git consistency does not prove installed-environment consistency.
- Show a `--dry-run` before every remote write, then authorize it under the active engineering
  mode for the named source, destination, and selector. The engineering envelope may authorize its commit, tag,
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

Set `RIGSYNC_SCRIPT` to the absolute path of this skill's own `scripts/rigsync.py` — see
"Resolving this skill's own files" above — then run:

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
`HEAD`, branch, remote URL, tag, and clean `code/`, `config/`, `scripts/`, dependency manifests,
Python/uv pins, and `sync.toml` afterward. `sweep-dispatch` combines this gate with
`environment-sync`. `push-source` never establishes Research 2.0 launch consistency and must not
be used by dispatch.

Artifact selectors are `<group>` or `<group>/<relative/path>`, where `<group>` is declared under
`[artifacts]` in `sync.toml`. `pull` means peer → current rig; `push` means current rig → peer.

## Setup and stop conditions

- Require project-local `sync.toml` and user-local `~/.config/rigsync/machines.toml`. For a new
  destination, preview and authorize `prepare` under the active engineering mode; with `[git]` configured it clones the hub remote
  into an absent/empty path and refuses a non-empty non-Git directory.
- Preserve existing registry entries. Add canonical rig aliases only from discovered SSH config
  and hostname facts or explicit user input.
- Keep project locations only in `sync.toml`; keep SSH identity only in the user registry.
- If Git/rsync/SSH, the project path, GitHub authentication, remote identity, or a machine mapping
  is missing, stop with the exact failed check. Never substitute `scp`, ad-hoc tarballs, or
  hand-written rsync.
