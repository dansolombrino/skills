# run_id migration reference

## What a migration touches

On every machine that holds the experiment: the hub, plus each rig named in a supervisor queue's
pool or ledger for the experiment's waves (a Slurm target such as `leonardo` included), plus any
`--machines` given to `migrate plan`.

- **Directory renames:** every child of `checkpoints|evaluations|scripts|logs/<exp>/` named
  `<old hash>` or `<old hash>.<suffix>`, such as a `.superseded-<wave_id>` quarantine. A nested
  `NNN_sub` experiment is skipped.
- **Text rewrites:** every exact `rid-<32 hex>` old hash is replaced by its new hash. This covers
  each text file of at most 4 MiB with no NUL byte under those four trees, including
  `.status.json` and `*.offloaded.json` receipts. It also covers
  `.waves/_state/<wave>/` (queue, ledger, snapshot, table, questions, lane buffers) for the
  experiment's waves. On the hub it covers `EXPERIMENTS.md` and `JOURNAL.md`, plus the rig-board
  `history.jsonl` and lane files, which are rewritten under the board lock and are not part of
  the staleness check. Binary checkpoints are never read.
- **Structural rewrites:** each `.run_id.json` gets
  `{layout, version: N+1, hash: new, params: new values}`, written byte for byte as the
  template's guard writes it. A backfilled run's `.run_config.json` gains its backfilled keys.
  `config/<exp>/RUN_ID_VERSIONS.json` and `config/<exp>/RUN_ID_HISTORY.json` are appended **on the
  hub only**: they are tracked source, the migration's one commit carries them, and a rig receives
  them with the next wave deploy (its worktree reads them through `source_path`). No rig file of
  that name is read or written. `RUN_ID_MAP.json` (in `evaluations/<exp>/`) is rebuilt on the hub,
  and on any rig that already has one, embedding the new `versions` and `history`.
- **Listed, not changed:** `plots/` files whose name or content holds an old hash (to re-render),
  and, only in a project whose runs logged to WandB before tracking became offline-only, its WandB
  runs. The plan detects them from a `wandb` section in the run configs or `wandb-metadata.json`
  under the run dirs; a project tracking offline only has neither, and gets no `wandb_rename.py`.
  Such a run was `wandb.init(name=run_id_label(...), tags=[run_id_name(...)], ...)`, so
  `wandb_rename.py` finds it by its old-hash tag, sets its display name to the new label, and
  replaces the old-hash tag with the new hash; the WandB run id never carries the hash and never
  changes.
- **Follow-up, not rewritten by `apply`:** EXPERIMENTS.md beyond its hash strings. `apply` writes
  `followups.json` (below); the agent updates the section preamble, the new param columns, and
  the label cells from it through `experiments-tracking`.

The plan file defaults to `evaluations/<exp>/.run_id_migrations/plan-v<N>-v<N+1>.json`.
`.run_id_migrations/` is never scanned. A plan written under one of the scanned trees makes
itself stale.

## Backfill file

```json
{
  "lr":    {"value": 0.001, "source": "code/000_exp/train.py:41@1a2b3c4",
            "runs": {"rid-<old hash>": {"value": 0.01, "source": "code/000_exp/train.py:44@5d6e7f8"}}},
  "optim": {"value": null, "source": "absent"}
}
```

The top-level `value`/`source` pair is the default for every run whose config lacks the param.
An entry under `runs` overrides that default for one old hash. `"absent"` means the param did
not exist yet, and its value must be `null`. A backfill for a run whose config already records
the param is refused.

## plan.json

```json
{
  "schema": "run-identity-migration/1", "created_at": "...", "project": "/abs", "experiment": "000_exp",
  "layout": "hashed-v2", "from_version": 1, "to_version": 2,
  "params_before": ["model", "seed"], "params_after": ["model", "seed", "lr"],
  "added": ["lr"], "removed": [], "reason": "...", "waves": ["20260921-093000"],
  "versions_entry": {"version": 2, "params": [], "date": "YYYY-MM-DD", "reason": "..."},
  "history_entry": {"from_version": 1, "to_version": 2, "date": "...", "map": {"old": "new"},
                    "backfill": {"lr": {"value": 0.001, "source": "...", "runs": {}}}},
  "map": {"old": "new"},
  "runs": {"old": {"new": "...", "params_before": {}, "params_after": {}, "label_before": "...",
                   "label_after": "...", "backfilled": {"lr": {"value": 0.001, "source": "..."}},
                   "wave_id": "...", "source_revision": "...", "source_tag": "..."}},
  "new_index": {"new": {"params": {}, "version": 2}},
  "machines": {"<name>": {"root": "/abs", "hub": true,
                          "renames": [{"from": "rel", "to": "rel"}],
                          "files": [{"path": "rel", "sha256": "...", "action": "replace|content",
                                     "hits": 3, "content_b64": "only for content"}],
                          "map_file": {"path": "rel", "sha256": null, "rebuild": true},
                          "runs_present": ["old"], "plots": [], "wandb_dirs": 0, "notes": [],
                          "inventory": {"renames": [], "files": [], "records": {}, "identity": {}}}},
  "board": {"files": [{"path": "/abs/history.jsonl", "hits": 1}]},
  "plots": ["plots/000_exp/plot_loss/loss.html"],
  "wandb": {"detected": true, "paths": ["entity/project"],
            "renames": [{"old_hash": "", "new_hash": "", "old_label": "", "new_label": ""}]},
  "notes": []
}
```

`apply` re-inventories every machine and refuses when anything under `inventory` differs.
`files[].sha256` is `null` for a file the migration creates, such as the first
`config/<exp>/RUN_ID_HISTORY.json`. The hub's `inventory.identity` covers
`config/<exp>/RUN_ID_VERSIONS.json`, `config/<exp>/RUN_ID_HISTORY.json` and
`evaluations/<exp>/RUN_ID_MAP.json`; a rig's covers only its map.

## rollback.json

The manifest is written at `evaluations/<exp>/.run_id_migrations/<ts>/rollback.json` on the hub,
next to a copy of the plan, before anything changes:

```json
{
  "schema": "run-identity-rollback/1", "ts": "YYYYMMDD-HHMMSS",
  "status": "prepared|applying|applied|failed-rolled-back|failed-rollback-incomplete|rolled-back|rollback-incomplete",
  "experiment": "000_exp", "from_version": 1, "to_version": 2, "map": {"old": "new"},
  "plan_sha256": "...", "board": {"files": ["/abs/history.jsonl"], "rewritten": true},
  "machines": {"<name>": {"root": "/abs", "backup_dir": "/abs/evaluations/<exp>/.run_id_migrations/<ts>/backup",
                          "renames": [{"from": "rel", "to": "rel"}],
                          "files": [{"path": "rel", "sha_before": "...", "sha_after": "..."}],
                          "state": "pending|applied|rolled-back"}}
}
```

Each machine keeps byte copies of its original files under its own `backup_dir`. Rollback first
renames each directory back, newest rename first. It then restores every file from its backup,
or deletes a file whose `sha_before` is `null`, and reverses the board rewrite. Before changing
anything it checks that each file still has its `sha_after` hash.

## followups.json

Written beside `rollback.json` once `apply` succeeds, and printed:

```json
{
  "schema": "run-identity-followups/1", "experiment": "000_exp", "from_version": 1, "to_version": 2,
  "params_before": ["model", "seed"], "params_after": ["model", "seed", "lr"],
  "experiments_md": "in the 000_exp section set the preamble's `run_id version:` ...",
  "runs": [{"old_hash": "...", "new_hash": "...", "label_before": "...", "label_after": "...",
            "params_after": {}, "backfilled": {"lr": {"value": 0.001, "source": "..."}},
            "wave_id": "..."}]
}
```
