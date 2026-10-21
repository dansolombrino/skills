---
name: rig-sync
description: Deploy exact tested Git revisions as per-wave worktrees and move artifacts across configured Research 2.0 GPU rigs, including the continuous hub-ward offload that keeps a disk-constrained rig usable. Use when validating declared rig paths, preparing a rig clone, copying the project .env or shell cache settings to a rig, materializing a wave commit as a worktree, checking cross-rig revision consistency, listing which waves are active on a machine, pruning finished wave worktrees and unused environments, transferring selected artifacts, collecting wave evaluations to the hub, offloading a supervised wave's checkpoints from a rig short on disk, restoring a run's checkpoints to the rig resuming it, or copying a declared shared-cache subtree to a rig. Pair with environment-sync; do not use for legacy projects, environment provisioning, or destructive Git recovery.
---

# rig-sync

`scripts/rigsync.py` moves source by Git and artifacts by rsync across the rigs declared in the
project's `sync.toml` and the user registry `~/.config/rigsync/machines.toml` (formats:
[references/configuration.md](references/configuration.md)). Each machine's `repo_path` is a main
checkout plus shared storage; a wave runs from its own worktree `.waves/<wave_id>` at its tag,
and deployment never moves the main checkout.

**Resolving this skill's own files.** `scripts/rigsync.py` means *this skill's installed
directory* — the folder holding the SKILL.md you are reading — not the research project, whose own
`scripts/` holds shell wave scripts only.

## Commands

```bash
RIGSYNC_SCRIPT=/absolute/path/to/rig-sync/scripts/rigsync.py
python3 "$RIGSYNC_SCRIPT" check-paths
python3 "$RIGSYNC_SCRIPT" repo-path --machine behemoth
python3 "$RIGSYNC_SCRIPT" storage-env --machine behemoth
python3 "$RIGSYNC_SCRIPT" doctor --machines rig-4090,rig-3090-ti,rig-3080-ti,behemoth
python3 "$RIGSYNC_SCRIPT" provision-env --machines rig-4090,rig-3090-ti,behemoth --dry-run|--confirm
python3 "$RIGSYNC_SCRIPT" prepare --machine rig-3090-ti --dry-run|--confirm
python3 "$RIGSYNC_SCRIPT" push-env --machine rig-3090-ti --dry-run|--confirm
python3 "$RIGSYNC_SCRIPT" deploy-revision --wave 20260802-120000 --revision <40-char-sha> --branch main --machines rig-4090,rig-3090-ti --dry-run|--confirm
python3 "$RIGSYNC_SCRIPT" verify-revision --wave 20260802-120000 --revision <40-char-sha> --branch main --machines rig-4090,rig-3090-ti
python3 "$RIGSYNC_SCRIPT" activity --machines rig-4090,leonardo
python3 "$RIGSYNC_SCRIPT" prune --machines rig-4090,leonardo --waves 20260802-120000 --dry-run|--confirm
python3 "$RIGSYNC_SCRIPT" offload --machine behemoth --wave 20260802-120000 --dry-run|--confirm
python3 "$RIGSYNC_SCRIPT" restore --machine rig-3090-ti --wave 20260802-120000 --run <run_id_name> --confirm
python3 "$RIGSYNC_SCRIPT" collect --wave 20260802-120000 [--machine behemoth] --dry-run|--confirm
python3 "$RIGSYNC_SCRIPT" collect --check --wave 20260802-120000
python3 "$RIGSYNC_SCRIPT" sync-cache --var HF_DATASETS_CACHE --subpath <relative subtree> --to rig-3090-ti --dry-run
python3 "$RIGSYNC_SCRIPT" status --group evaluations
python3 "$RIGSYNC_SCRIPT" pull evaluations/000_exp --from rig-3090-ti --dry-run|--confirm
```

Every write has a `--dry-run`. `rig-4090` is local when its name or hostname matches the current
host; peers get bounded, non-interactive SSH.

- `check-paths` — each `repo_path` sits under one of its rig's declared storage roots. Run it
  before the first remote step: it catches a misplaced path before anything is cloned there.
- `repo-path` / `storage-env` — read a rig's project path and storage floor back; never retype
  them.
- `doctor` — before dispatch: hostname identity, Git (2.17+ for worktrees), storage roots
  resolved and mounted, quota headroom (`quota`, not `df` alone), shared cache values visible to a
  non-interactive shell, and no project `.env` redeclaring them.
- `provision-env` — writes the rig's declared shared-cache variables into a marked block of
  `.zshenv`/`.bashrc`, backs both up, verifies a non-interactive shell, and restores the backup
  when that fails. On a shared machine it changes that account's every future shell.
- `prepare` — clones the hub remote into an absent or empty `repo_path`.
- `push-env` — copies the hub's `.env` to a declared peer (Git leaves a fresh clone without one):
  lands 0600, prints key names only, does nothing when identical, and keeps a different peer file,
  naming the differing keys, unless `--overwrite`.
- `deploy-revision` — requires the remote tag `wave--<wave_id>` to name the SHA and the branch to
  contain it, then `git worktree add --detach .waves/<wave_id>`; an existing worktree is only
  verified. `verify-revision` checks the worktree is this repo's, on the tag, and clean in `code/`,
  `config/`, `scripts/`, and the dependency manifests. Always include the hub. Both refuse a hub
  that does not ignore `.waves/` and `.envs/`.
- `activity` — read-only: per machine, this project's tmux lanes, `running` statuses, and Slurm
  jobs grouped by wave, plus worktrees and keyed environments.
- `prune` — removes the named waves' worktrees (never forced) and every `.envs/<env_key>` no
  remaining worktree uses; refuses a wave with activity, unattributable activity, a dirty
  worktree, or one the supervisor still has registered without `finished_at`. A pruned wave stays
  recoverable from its tag.
- `offload` — one pass of the hub-ward copy for a supervised wave on a rig short on disk: copies
  checkpoint and evaluation files older than two minutes to the hub, then frees a rig checkpoint
  only when it is this wave's, settled, not the newest checkpoint of an unfinished run, and
  SHA-256-identical on the hub, leaving `<file>.offloaded.json` first. Evaluations are kept. It
  refuses when the hub is under its own floor. `restore` copies one run's checkpoints back to the
  rig that will resume it.
- `collect` — copies a wave's evaluation records (`.run_id.json`, `.run_config.json`,
  `.status.json`, metrics) from every pool rig to the hub; never deletes, never copies
  checkpoints, sha256-verifies each file. A finished hub record is not overwritten (a later
  rerun's files replace a `failed` attempt); a `.run_id.json`/`.run_config.json` that differs is a
  hard error for a person to settle. `collect --check` changes nothing and lists rig records the
  hub lacks or holds differently: the "reconciled" precondition of the dispatch run_id gate and
  `runid migrate plan`. A rig it cannot reach passes only for a finished wave whose final collect
  from it succeeded.
- `sync-cache` — copies one relative subtree of a declared shared cache hub → rig, under a lock,
  never deleting.
- `pull`/`push` — artifact selectors `<group>` or `<group>/<path>` from `[artifacts]`; `pull` is
  peer → here. A cluster with `transfer_ssh` declared moves data through that host.

## Limits

- A rig without a registry entry, or a destination not in both files, is not a target; storage
  roots and caches are declared by the user, never inferred or added to make a check pass.
- `push-source` is not a wave deployment; waves use `deploy-revision`.
- `prune` and `offload` are the only deletions. A failed check is reported, not repaired: no
  force-push, reset, stash, clean, merge, `scp`, tarball, or hand-written rsync.
