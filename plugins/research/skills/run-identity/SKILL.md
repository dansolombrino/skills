---
name: run-identity
description: Own the hashed-v2 run_id identity of a Research 2.0 experiment - elect its identity params, look up a run hash or prefix (old hashes resolve through RUN_ID_HISTORY.json) or find the runs matching param values, and migrate an experiment to the next run_id version when a sweep adds or drops an identity axis, renaming every old hash on the hub and every rig with a rollback manifest. Use when the user pastes or asks about a rid- hash, asks which run has given params, when a new experiment needs its run_id params elected, or when check_run_id_plan or guard_run_config refuses a wave and says to propose the next run_id version. Do not use for legacy-pinned layouts (segments-v2/v1, nested, collapsed-v1, hashed-v1), which never migrate.
---

# run-identity

Under `hashed-v2` a run's identity is `rid-<32 hex>`: sha256 of the canonical JSON of its identity
params, read as top-level keys of the run's resolved config (a missing key hashes as null). The
identity files are tracked source in the hub checkout, beside the experiment's hydra configs: the
current params are the last entry of `config/<exp>/RUN_ID_VERSIONS.json`, and every renaming is
recorded in `config/<exp>/RUN_ID_HISTORY.json`, so an old hash in git history, a tag, a plot or a
chat always resolves. Experiment code reads them through `source_path("config/<exp>")`, so each
wave worktree pins the version it was deployed with and no rig keeps its own copy.
`evaluations/<exp>/RUN_ID_MAP.json` is derived, on every machine, and embeds copies of both files. Canon: `../research-project-init/references/conventions.md`. Plan and manifest shapes:
[references/migration.md](references/migration.md).

**Resolving this skill's own files.** `scripts/runid.py` means *this skill's installed directory*,
not the research project. It loads `rig-sync` and `rig-board` by sibling path and pipes its
worker `scripts/runid_agent.py` to every machine over the rig-sync transport, so a rig needs
nothing installed.

```bash
RUNID=/absolute/path/to/run-identity/scripts/runid.py
python3 "$RUNID" --project <root> --experiment <exp> lookup <hash or prefix> [--json]
python3 "$RUNID" --project <root> --experiment <exp> lookup --where lr=1e-4 model=mlp [--json]
python3 "$RUNID" --project <root> --experiment <exp> migrate plan --add optim,lr --reason "<why>" [--remove p] [--backfill-file f.json]
python3 "$RUNID" --project <root> --experiment <exp> migrate apply --plan <plan.json> --dry-run|--confirm
python3 "$RUNID" --project <root> --experiment <exp> migrate rollback <ts> --dry-run|--confirm
```

## Lookup

`lookup` takes a full hash, a unique prefix (with or without `rid-`), or an old hash; it prints
params, version, wave, state and rig from `RUN_ID_MAP.json` (falling back to the run's own
records). History comes from `config/<exp>/RUN_ID_HISTORY.json`, or from the map's embedded copy
when the checkout lacks it. Without `--experiment` it searches every hashed-v2 experiment (an
`evaluations/` dir holding a map, or a `config/` dir holding `RUN_ID_VERSIONS.json`). `--where k=v`
values parse as JSON when they can, so `lr=1e-4` matches a stored `0.0001` and `seed=1.0` matches
`1`; a dotted key (`optim.lr`) reads the stored config. Exit codes, for both forms: **0** found,
**1** nothing matched (a hash, prefix, or `--where` query with no run; also a hash known only from
history with no record here), **2** an error (an ambiguous prefix, a malformed argument, no
hashed-v2 experiment, an unreadable file).

## Electing the params (new experiments)

Decided by the agent, with no prompt: **every param the grid varies, plus every behavior-affecting
param whose value differs from the config default.** Elect **top-level config keys only**; to
identify a nested value, elect its top-level subtree key (`optim`, never `optim.lr`) — `migrate
plan` and the template's `load_run_id_spec` refuse a dotted param. Write version 1 to
`config/<exp>/RUN_ID_VERSIONS.json` with the reasoning in its `reason`, commit it with the
experiment's code, and record the same reasoning in the JOURNAL.md entry. Never add a param that
only labels (a run name, a notes field) or that cannot change results.

## Migration protocol

Trigger: a sweep must vary a param that is not in the identity, or `check_run_id_plan` /
`guard_run_config` refuses with "propose the next run_id version". Propose the version, then:

1. **Plan.** `migrate plan --add ... --reason ...`. It refuses, with a message saying why, while
   any wave of the experiment is live, any rig that ran it is unreachable, or `rigsync collect
   --check` finds rig records missing or different on the hub; it also refuses a legacy layout and
   a dotted param. `collect --check` needs every rig of the earlier waves reachable: when one is
   not (for example an expired `leonardo` certificate), restore access (renew the certificate) and
   rerun. There is no bypass.
2. **Backfill.** Runs whose stored `.run_config.json` lacks a new param are listed with their
   `source_revision`, `source_tag` and wave. For each, read the code at that commit
   (`git show wave--<wave_id>:<file>`) and record the value it ran with, sourced as
   `file:line@sha`; a param that did not exist yet is `null` with source `"absent"`. Ask the user
   only when the code cannot settle it. Rerun the plan with `--backfill-file`.
3. **Show the plan summary** (counts per machine, the old → new map, every backfill with its
   source, plots to re-render, WandB renames) and get the user's **explicit approval**. This gate
   holds in both engineering modes.
4. **Apply** with `--confirm`. It re-verifies the preconditions and that nothing changed since the
   plan, writes the rollback manifest and per-machine backups first, renames run dirs and rewrites
   every file naming an old hash on the hub and each rig, appends the version and history entries
   to the hub's tracked `config/<exp>/` files (never on a rig: the next wave deploys the new
   revision there), rebuilds `RUN_ID_MAP.json` (with the new versions and history embedded) and
   checks every machine's index equals the planned map. Any failure rolls back what was applied,
   then reports. It prints, per run, the new hash, the new label and the backfilled values, and
   writes them to `followups.json` beside `rollback.json`.
5. **Follow-ups**, in this order: run the generated `wandb_rename.py` with the project environment,
   `<environment.name>/bin/python wandb_rename.py` (without, then with, `--apply`; `apply` prints
   the path with the name from `sync.toml`): it finds each run by its old-hash tag, sets its display
   name to the new label and swaps the old-hash tag for the new hash; re-render the listed plots
   (`visualizations`); **update EXPERIMENTS.md** through `experiments-tracking` from
   `followups.json` — the section preamble's `run_id version:` and params line, the new param
   columns, and each row's param values and label cell (`apply` only replaced the hash strings);
   append the JOURNAL.md entry (reason, map, backfill sources); make one commit with
   `config/<exp>/RUN_ID_VERSIONS.json`, `config/<exp>/RUN_ID_HISTORY.json`, EXPERIMENTS.md and the
   other rewritten tracked files, which the pre-commit journal hook accepts.

Backfilled values are also written into each run's `.run_config.json`, so resuming a migrated run
with the new config passes the guard.

## Rule exceptions

A run_id migration is the one change that may rewrite old hashes in JOURNAL.md, EXPERIMENTS.md
rows, wave `README.md` / `wave.sh` / `leonardo.jobs`, and supervisor state; wave recovery then
compares against the post-migration commit. Git history and tags are never rewritten:
`RUN_ID_HISTORY.json` resolves the hashes they hold.

## Rollback

`migrate rollback <ts> --confirm` (the timestamp under `evaluations/<exp>/.run_id_migrations/`)
restores byte-identical trees on every machine, which also removes the appended version and
history entries. Only the latest migration rolls back, and only while nothing it touched has
changed since; `--force` overrides that check and needs the user. Then journal it and revert or
drop the migration commit.
