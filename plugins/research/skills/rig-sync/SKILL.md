---
name: rig-sync
description: Deploy exact tested Git revisions as per-wave worktrees and synchronize selected artifacts across configured Research 2.0 GPU rigs. Use when preparing an approved empty rig clone, verifying GitHub and rig identity, materializing an authorized wave commit as its own worktree, checking cross-rig revision consistency, listing which waves are active on a machine, proposing or running an approved prune of finished wave worktrees and unused wave environments, or transferring selected artifacts. Pair with environment-sync; do not use for legacy projects, environment provisioning, continuous mirroring, destructive Git recovery, deletion of anything but approved prune targets, or writes outside an approved engineering envelope.
---

# rig-sync

Run this skill's bundled `scripts/rigsync.py` against a structured research-project root. Read
[references/configuration.md](references/configuration.md) when creating or repairing `sync.toml`
or the user registry. Git/GitHub distributes launch source; rsync moves selected artifacts.

Each machine's `repo_path` is a main checkout plus the shared storage tree. A wave never executes
from the main checkout: `deploy-revision` gives it a detached worktree `.waves/<wave_id>` pinned
to its `wave--<wave_id>` tag, and deployment never moves the main checkout. Waves of different
revisions therefore coexist on one machine.

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

- Run `check-paths` before the first remote step on a project, `doctor` before dispatch,
  `verify-revision` before every initial/recovery launch, and `status` before artifact movement.
  Run `environment-sync verify` separately after revision deployment; Git consistency does not
  prove installed-environment consistency.
- Show a `--dry-run` before every remote write, then authorize it under the active engineering
  mode for the named source, destination, and selector. The engineering envelope may authorize its commit, tag,
  push, and worktree deployment to the named rigs.
- Never force-push, reset, stash, clean, merge, add `--delete`, delete remote files, or infer an
  SSH alias/path. The single deletion this skill performs is `prune` (below), and only with
  explicit user approval of its dry run in every engineering mode.
- Treat `rig-4090` as local when its configured name or hostname matches the current host; do not
  require self-SSH. Use bounded, noninteractive SSH for peers.
- Treat a configured registry `hostname` as an identity assertion: `doctor` must compare it with
  the selected machine's observed hostname and fail on drift.
- Treat the registry's storage roots and `caches` the same way: a rig's volumes and shared cache
  paths are **declared, never guessed**, and never defaulted to `$HOME`/`~/.cache`. A rig may
  declare several roots (`storage_roots`; the single `storage_root` form still works). A project's
  root is the declared root containing its resolved `repo_path`; never add a root to the registry
  to make a path pass. `doctor` resolves `repo_path` before comparing, fails a root that sits on the
  system filesystem (an unmounted volume), checks that root's headroom against its quota rather
  than `df` alone,
  probes a **non-interactive** shell for the cache values, and fails a project `.env` that
  re-declares any of them.
- `provision-env` edits shell startup files on the rig, so it is a protected write: show its
  `--dry-run` and authorize under the active engineering mode before `--confirm`. On a shared
  machine, say so explicitly — the change affects that account's every future shell.
- `push-env` places the hub's `.env` on a peer that Git left without one. It is a protected write
  that **moves secrets**: say so, show the `--dry-run`, and never pass `--overwrite` to clobber a
  peer's existing file without explicit approval.
- Never spell a rig's project path or storage floor by hand. Read them back with `repo-path` and
  `storage-env`; a second copy of a declared fact is a second thing that can be wrong.
- Stop dispatch when `doctor` fails for an assigned rig.
- Refuse deployment when the main checkout's branch/remote differs, the hub does not ignore
  `.waves/` and `.envs/` (the wave-isolation contract is missing: say so), the remote tag does not
  name the approved SHA or the remote branch does not contain it, or the repository uses
  unconfigured submodules/Git LFS. Never repair an existing worktree: `verify-revision` reports
  its drift and the wave stops.
- Git on every machine must support `git worktree remove` (2.17 or newer); `doctor` checks it.

## Commands

Set `RIGSYNC_SCRIPT` to the absolute path of this skill's own `scripts/rigsync.py` — see
"Resolving this skill's own files" above — then run:

```bash
python3 "$RIGSYNC_SCRIPT" check-paths
python3 "$RIGSYNC_SCRIPT" repo-path --machine behemoth
python3 "$RIGSYNC_SCRIPT" storage-env --machine behemoth
python3 "$RIGSYNC_SCRIPT" doctor --machines rig-4090,rig-3090-ti,rig-3080-ti,behemoth
python3 "$RIGSYNC_SCRIPT" provision-env --machines rig-4090,rig-3090-ti,behemoth --dry-run
python3 "$RIGSYNC_SCRIPT" provision-env --machines rig-4090,rig-3090-ti,behemoth --confirm
python3 "$RIGSYNC_SCRIPT" prepare --machine rig-3090-ti --dry-run
python3 "$RIGSYNC_SCRIPT" prepare --machine rig-3090-ti --confirm
python3 "$RIGSYNC_SCRIPT" push-env --machine rig-3090-ti --dry-run
python3 "$RIGSYNC_SCRIPT" push-env --machine rig-3090-ti --confirm
python3 "$RIGSYNC_SCRIPT" deploy-revision --wave 20260802-120000 \
  --revision <40-char-sha> --branch main --machines rig-4090,rig-3090-ti --dry-run
python3 "$RIGSYNC_SCRIPT" deploy-revision --wave 20260802-120000 \
  --revision <40-char-sha> --branch main --machines rig-4090,rig-3090-ti --confirm
python3 "$RIGSYNC_SCRIPT" verify-revision --wave 20260802-120000 \
  --revision <40-char-sha> --branch main --machines rig-4090,rig-3090-ti
python3 "$RIGSYNC_SCRIPT" activity --machines rig-4090,leonardo
python3 "$RIGSYNC_SCRIPT" prune --machines rig-4090,leonardo --waves 20260802-120000 --dry-run
python3 "$RIGSYNC_SCRIPT" prune --machines rig-4090,leonardo --waves 20260802-120000 --confirm
python3 "$RIGSYNC_SCRIPT" status --group evaluations
python3 "$RIGSYNC_SCRIPT" pull evaluations/000_exp --from rig-3090-ti --dry-run
python3 "$RIGSYNC_SCRIPT" pull evaluations/000_exp --from rig-3090-ti --confirm
```

`deploy-revision` requires the remote `wave--<wave_id>` tag to name the approved SHA and the remote
branch to contain it (the branch may have moved on — tracking commits, later waves — which is what
lets recovery redeploy an old wave). It fetches the tag and runs
`git worktree add --detach .waves/<wave_id> <sha>` where the worktree is absent; an existing one is
only verified. `verify-revision` requires the worktree to belong to this `repo_path`, its `HEAD`
and the tag to name the SHA, and its `code/`, `config/`, `scripts/`, dependency manifests,
Python/uv pins, and `sync.toml` to be clean; it also checks the main checkout's branch and remote.
Always include the hub in both: it is the fingerprint reference. `sweep-dispatch` combines this
gate with `environment-sync`.

`activity` is read-only: per machine it groups this project's tmux lanes, `running` statuses, and —
on a `gpus = "job"` machine — queued or running Slurm jobs submitted from `repo_path`, by wave id,
and lists the machine's wave worktrees and keyed environments. Evidence without a wave id is shown
under `?`.

`prune` removes the named waves' worktrees with `git worktree remove` (never forced), then every
`.envs/<env_key>` no remaining worktree uses; it refuses a wave with any activity, any activity it
cannot attribute to a wave, and a dirty worktree, and leaves unrecognized `.envs/` entries alone.
It is proposed by `sweep-dispatch` when a wave ends and by `experiments-tracking` reconciliation,
and runs with `--confirm` only after the user approves the dry run. A pruned wave stays
recoverable: `deploy-revision` recreates its worktree from the tag. `push-source` never establishes Research 2.0 launch consistency and must not
be used by dispatch.

Artifact selectors are `<group>` or `<group>/<relative/path>`, where `<group>` is declared under
`[artifacts]` in `sync.toml`. `pull` means peer → current rig; `push` means current rig → peer.

## Setup and stop conditions

- Require project-local `sync.toml` and user-local `~/.config/rigsync/machines.toml`. The registry
  is written once per machine and shared by every project on it, so a rig with no entry cannot be
  declared at all. Require `check-paths` to pass before `prepare`: it catches a `repo_path` under
  none of the rig's storage roots while that is still a one-line edit rather than a misplaced clone.
- For a new destination, preview and authorize `prepare` under the active engineering mode; with
  `[git]` configured it clones the hub remote into an absent/empty path and refuses a non-empty
  non-Git directory. Follow it with `push-env`: `.env` is ignored by Git, so the fresh clone has
  none and nothing else will put one there.
- Preserve existing registry entries. Add canonical rig aliases only from discovered SSH config
  and hostname facts or explicit user input.
- Keep project locations only in `sync.toml`; keep SSH identity only in the user registry.
- If Git/rsync/SSH, the project path, GitHub authentication, remote identity, or a machine mapping
  is missing, stop with the exact failed check. Never substitute `scp`, ad-hoc tarballs, or
  hand-written rsync.
