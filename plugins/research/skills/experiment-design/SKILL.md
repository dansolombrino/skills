---
name: experiment-design
description: Compile an approved scientific step into a reproducible Research 2.0 experiment with NNN naming, agent-decided hashed-v2 run-id election, checkpoints, schema-v3 status telemetry, smoke criteria, and WandB decisions. Use when creating a new experiment or sub-experiment, writing training, finetuning, or evaluation code under code/NNN_..., or adding its Hydra configuration; do not use to migrate legacy experiment layouts.
---

# experiment-design

Protocol for compiling an approved scientific step into a new experiment. Read
`../research-project-init/references/conventions.md` and the active
`program/00-execution-agreement.md` first. Stop as unsupported when Research 2.0 surfaces are
absent; do not offer migration.

Directives are closed (see the canon section of that name). Derive naming, structure, config
shape, and defaults from these directives and this project alone. Never read another project on
disk to copy how an experiment was laid out there; where a directive is missing, ask the user.

Technical choices are mode-owned (conventions, "Approval tiers"): in manual mode, propose each
technical choice and wait for the user; in auto mode, choose and record it inside the approved
engineering envelope. The run_id election is agent-decided in both modes (section 2). Integrity
gates and the always-ask actions never become delegable.

## 1. Name & mirrored structure

- Propose the next `NNN_[experiment_name]` (zero-padded, next free number; sub-experiments nest as `NNN_exp/NNN_sub_exp`).
- Treat the complete numbered leaf hierarchy as `<experiment_path>` and preserve it unchanged in
  every engineering handoff. Once authorized by the active mode, create the matching dirs in
  `code/` and `config/`; later `evaluations/`, `visualizations/`, and `plots/` paths must retain the
  same hierarchy as their artifacts appear. Apply `visualizations` for plotting-code placement.
- Unit tests for an experiment script are optional and create-on-demand. When one is written, it
  belongs at `tests/code/<experiment_path>/<script_stem>/test_*.py` (pytest). This is not the
  pre-dispatch smoke test of section 4c and never substitutes for it. Do not scaffold the tree and
  do not ask whether tests are wanted.

## 2. run_id election

Agent-decided in both engineering modes (conventions, "Approval tiers": do, then report). There is
no rendering dialogue. `run-identity` owns the election rules, lookup, and migration.

1. List the experiment's config params and elect the identity: **every param the sweep grid
   varies plus every behavior-affecting param whose value differs from the config default**. State
   the elected params and the reasoning for each inclusion and exclusion in the reply and the
   journal entry; the user may correct it. Identity params are **top-level config keys only**; to
   identify a nested value, elect its top-level subtree key (`optim`, never `optim.lr`).
2. New experiments always use the `hashed-v2` layout. Record `RUN_ID_PATH_LAYOUT = "hashed-v2"` in
   the experiment's `.py` and write `config/<experiment_path>/RUN_ID_VERSIONS.json` as
   `[{"version": 1, "params": [...], "date": "<YYYY-MM-DD>", "reason": "<why>"}]` — tracked source
   beside the experiment's hydra configs, never `evaluations/` (gitignored, rig-local), so every
   wave worktree carries the identity version it was deployed with. The code reads it with
   `spec_root = source_path("config/<experiment_path>")` and `version, params =
   load_run_id_spec(spec_root)`; there are no `RUN_ID_PARAMS`/`RUN_ID_SEGMENTS` constants under
   `hashed-v2`.
   Mirror the pin, the version and its params, and the
   `run_id map: evaluations/<experiment_path>/RUN_ID_MAP.json` line in the experiment's
   EXPERIMENTS.md section header.
3. **Paths and names.** A run's identity is `rid-<32 hex>`: the first 32 hex digits of sha256 over
   the canonical, sorted-key JSON of `{"layout": "hashed-v2", "params": {param: value or null}}`,
   so param order never matters and a missing param hashes as `null`. Make ALL run-scoped paths
   and names go through `code/common/run_id.py`: checkpoints, evaluations, and the `scripts/` and
   `logs/` run folders use `run_id_path(cfg, params, layout=RUN_ID_PATH_LAYOUT)` (the one directory
   `rid-<32 hex>`); the EXPERIMENTS.md `run_id` column and the WandB hash tag use
   `run_id_name(...)`, the hash; `run_id_label(cfg, params)` gives the readable `k=v,…` used as the
   WandB display name and the EXPERIMENTS.md `label` column. WandB, one exact form:
   `wandb.init(name=run_id_label(...), tags=[run_id_name(...)], config=wandb_config(cfg))` — the
   hash is a tag (36 characters, within WandB's 64-character tag limit), never the WandB run id.
   Never hand-build a path or mix layouts across run-scoped surfaces.
4. **Collision guarantee.** 128 bits are never assumed collision-free on faith: the helper writes
   `{layout, version, hash, params}` to `.run_id.json` inside every run directory and hard-fails
   when a directory's record names different params. `check_run_id_plan` rescans every existing
   record before dispatch and refuses a hash collision, a duplicate, an overflow, or a planned run
   whose path already holds a different `.run_config.json`; `write_run_id_map` rebuilds the
   experiment-wide map `evaluations/<experiment_path>/RUN_ID_MAP.json` (`by_hash`, `by_params`,
   `configs`, and copies of `versions`/`history`) once per wave, hard-failing when one hash maps to two param sets. The per-run guard
   never scans other runs or writes the map: its I/O stays O(1) and atomic, so thousands of
   concurrent runs on a shared filesystem neither slow down quadratically nor read a torn file. A
   collision stops the work; it is never resolved by reusing, renaming, or rewriting a directory.
   The map is a derived index, never the identity source; `run-identity` reads a hash back into its
   params (following `RUN_ID_HISTORY.json` for an old hash) and vice versa.
5. Wire `guard_run_config(cfg, params, <eval run dir>, layout=RUN_ID_PATH_LAYOUT,
   experiment_root=<evaluations experiment dir>, spec_root=source_path("config/<experiment_path>"))`
   into every training/eval script, **before the
   StatusWriter starts and before any artifact is written** — it checks the params against the
   current `RUN_ID_VERSIONS.json` entry, snapshots the full config to `.run_config.json`,
   hard-fails on run_id collisions (same run_id, different config), and atomically writes
   `.run_id.json`; it never touches `RUN_ID_MAP.json`, which the wave's end rebuilds once.

**Legacy pins.** `segments-v2`, `segments-v1` (the percent-encoded segments layout), `nested`,
`collapsed-v1`, and `hashed-v1` remain valid `RUN_ID_PATH_LAYOUT` literals only for experiments
that already recorded them, with `RUN_ID_PARAMS` (and `RUN_ID_SEGMENTS` for the segments layouts)
in the `.py` and every legacy rule in conventions § run_id, "Legacy pins"; the helper keeps
rendering them unchanged. Every election under such a pin ran the legacy segments rendering
dialogue described there, but a re-election is only possible before the first output, and a fresh
election uses `hashed-v2`. Never offer a legacy pin to a new experiment.

## 2b. run_id evolution — config changes to an EXISTING experiment

Any change that adds/removes/renames a behavior-affecting config param (integration, new feature, refactor) **invalidates the election** — new runs would collide with old artifacts at the same `<run_id path>` (overwritten, or silently skipped as "done" by a self-guarded wave script). Full rules: `conventions.md`, "run_id schema evolution". Protocol:

1. **STOP before any new run launches** and re-run the election: does the new param join the
   identity? It may stay out only by explicitly recording why it is non-identifying.
2. Inspect checkpoints, evaluations, plots, and wave records for this experiment.
   If none exists, rewrite the current `RUN_ID_VERSIONS.json` entry (legacy: `RUN_ID_PARAMS`) and
   the EXPERIMENTS.md section header before the first launch, and report it.
3. Once any checkpoint, evaluation, or plot output or wave README/script exists:
   - **`hashed-v2`** — never edit a version in place. Propose the next identity version and a
     **run_id migration** through `run-identity`: its plan backfills the new params for old runs
     (from `.run_config.json`, or from the code at their wave tag with the source cited), refuses
     a collision, and lists every rename and rewrite. The migration **always asks**: apply it only
     after the user approves the plan. It renames the old runs to their new hashes everywhere,
     WandB runs included; the experiment stays one experiment.
   - **Legacy pins** — `RUN_ID_PARAMS` and `RUN_ID_SEGMENTS` are immutable under every legacy
     layout (`segments-v2`, `segments-v1`, `nested`, `collapsed-v1`, `hashed-v1`). Never change the
     identity schema in place. Create a new numbered sub-experiment with the re-elected params and
     its own `hashed-v2` identity; leave the old experiment and all of its records untouched.

## 2c. layout immutability for an EXISTING experiment

Inspect checkpoints, evaluations, plots, and wave records first. If none exists, run section 2 in
full and record `hashed-v2`. Once any output or wave record exists, preserve the checked-in
renderer and `RUN_ID_PATH_LAYOUT` forever (and, under a legacy pin, `RUN_ID_SEGMENTS`). Never
propose or perform an in-place layout change, including moving a legacy `segments-v2`,
`segments-v1`, `nested`, `collapsed-v1`, or `hashed-v1` experiment to another layout or regrouping
its segments. To use another rendering, create a new numbered sub-experiment and leave the old
experiment untouched. A run_id migration changes a `hashed-v2` identity's params, never its layout.

## 3. Config

- One hydra yaml tree under `config/NNN_exp/` holding ALL parameters with defaults. Pure params — no run_id metadata, no secrets (those are `.env`'s; new env var ⇒ update `.env.example` same turn).

## 4. Checkpoint checklist (must resolve)

Resolve every item with the applicable engineering-mode owner; do not leave implicit defaults.

1. Checkpoint filenames? (no repo-wide default — per-case)
2. Contents? Present trade-offs: full training state (model+optimizer+scheduler+RNG+step+config snapshot; exact resume) vs weights-only (small; cannot truly resume).
3. Resume support in this script, yes/no? — decided NOW, before coding; sweep-rerun behavior later inherits this silently — **including unattended crash recovery**: after a machine crash/reboot the sweep machinery auto-relaunches interrupted runs, and resumable ones continue from checkpoint while non-resumable ones restart from scratch, so choose full training state for anything expensive.
4. Which checkpoints to produce / retention? **Compute the footprint before answering**, and
   state it in the decision: `checkpoint size × checkpoints retained × runs in the sweep`. Full
   training state (item 2) is typically several times the model size, and "keep every step" is a
   retention policy — usually an unexamined one. Multiply it out against the rig's declared
   headroom (`rig-sync` → `references/configuration.md`) **and the hub's**: a rig short on disk is
   still used, because `rig-sync offload` keeps moving a wave's checkpoints to the hub, but the
   hub must then hold the whole sweep, and a run's own retained footprint must still fit the rig
   while it runs. Record the **per-run retained footprint in KiB**; each wave script adds it to
   its lane's storage floor. If the total is
   uncomfortable, the lever is here — keep last-N plus the final, checkpoint on a coarser cadence,
   or store weights-only for the intermediates and full state only where resume must work.
4a. **Write every checkpoint atomically**: to a temporary name in the same directory, then
   `os.replace` onto the final name, the way `code/common/status.py` writes status. A checkpoint
   that is copied, offloaded, or resumed from while half-written is indistinguishable from a real
   one until it is loaded. Temporary names end in `.tmp` so offload never touches them.
5. **Expected final artifact** — which file (final checkpoint and/or final eval output) proves the run truly finished? Prefer a small evaluation output: evaluations always stay on the rig that produced them, while an offloaded final checkpoint is represented there by its receipt. This is the **golden completion signal** monitors check first (`conventions.md`); record the choice in the experiment's EXPERIMENTS.md section header.

## 4a. Placement needs (must resolve)

A wave is one queue that any lane of its pool may serve, so record in the experiment's
EXPERIMENTS.md section header what decides where a run **can** go, never where it should:

- **`min_vram_mib`** — the smallest card a run fits, per config shape when the grid varies model
  or batch size. Measure it in the smoke or state it as an estimate; absent means "fits anywhere",
  and the supervisor tightens it after an out-of-memory failure.
- **`data needs`** — one tag per dataset or pretrained weight a run reads
  (`hf:<repo>`, `torch:<name>`, `file:<cache-relative path>`) with the declared cache variable it
  lives under and its size. A run is only handed to a rig where its tags are marked ready;
  fetching or copying them is the supervisor agent's job, not the user's.

## 4b. Run signaling & timing (mandatory, no user decision needed)

Every training/eval script wraps its work in the **StatusWriter** pattern
(`code/common/status.py`; canonical implementation in
`research-project-init` references/templates.md):

- owns `evaluations/NNN_exp/<run_id path>/.status.json` schema v3: state, timezone-aware
  lifecycle timestamps, live `elapsed_s`, automatic 60-second heartbeat, display progress, and
  numeric `progress_completed`/`progress_total`/`progress_unit`, plus `wave_id`, `rig`, `gpu`,
  `source_revision`, `source_tag`, and `environment_fingerprint`, read from environment exported
  by the source/environment-verified wave script — env vars, never config params, so they stay
  out of the `guard_run_config` snapshot;
- calls `heartbeat(completed=<done>, total=<total>, unit=<label>)` after every completed
  epoch/major step. The helper supplies liveness between calls; experiment code supplies the
  facts needed for ETA. Do not write an ETA into `.status.json`;
- prints start time, end time, and elapsed to stdout — the wave script tees all stdout+stderr to the mirror of its own path under `logs/` (`logs/NNN_exp/<run_id folder>/wave_<wave_id>/wave_<rig>_gpu<ids>-<timestamp>.log`; `rid-<32 hex>` under `hashed-v2`, the rendered path under a legacy segments pin, `<run_id_flat>` under the other legacy pins), so scripts need no separate file logging, and the elapsed lands in EXPERIMENTS.md as the run's reference runtime.

Before dispatching, require the canonical structured-telemetry helper (schema v3, or schema v2 in a project scaffolded before `rig`) and numeric call sites. A legacy
display-only status contract makes the repository unsupported; do not upgrade it in place.

## 4c. Pre-dispatch smoke test (must resolve)

1. Resolve one repeatable hub command that exercises the real entrypoint with the smallest safe
   workload and cannot collide with a production run_id or final artifact. Write its interpreter
   as the placeholder `<env>/bin/python`, never a literal environment directory: `sweep-dispatch`
   substitutes the keyed environment of the staged lock.
2. Resolve its pass criterion: exit zero plus the named lightweight output/assertion that proves
   initialization and one meaningful unit of work completed.
3. Record both in the experiment's EXPERIMENTS.md section header. `sweep-dispatch` reruns this
   exact command after staging the wave and before committing/tagging its Git revision; any source
   rewrite or failed criterion blocks the launch.

## 5. WandB checklist (must resolve)

1. Use wandb for this experiment?
2. **The ENTIRE resolved config is logged** — `wandb.init(config=wandb_config(cfg), ...)` from
   `code/common/run_id.py`, which returns the same full payload as the `.run_config.json` snapshot
   plus a `provenance` namespace (`wave_id`, `rig`, `gpu`, `source_revision`, `source_tag`,
   `environment_fingerprint`, from wave env vars). Never a hand-picked subset, never the identity
   params only: a finished run's config **cannot be changed retroactively** (a run_id migration
   renames runs, it never refills a config), so a partial config leaves the sweep permanently
   unfilterable on exactly the params nobody thought to log — and
   provenance is what lets you filter by tested commit, rig/GPU, and environment. Not a mode
   decision; no owner may narrow it.
3. Resolve metrics and their names/keys with the applicable engineering-mode owner.
4. Use project = research project, group = `NNN_experiment`. Under `hashed-v2` the one form is
   `wandb.init(name=run_id_label(...), tags=[run_id_name(...)], config=wandb_config(cfg))`: the
   display name is the readable label and the hash is a tag, never the WandB run id. A legacy pin
   keeps `name=run_id_name(...)` (the rendered or flat run_id). These follow from the run_id
   election and need no separate decision; a run_id migration renames the display name and swaps
   the hash tag.
5. Online mode on all rigs; `WANDB_API_KEY` from `.env`.
6. wandb files go under `logs/` — `wandb.init(dir=project_path("logs"))` — never the project root.

## 5b. Paths in experiment code (canon)

Waves run the code from a detached worktree while storage stays in the shared project root
(conventions, "Wave isolation"). So every storage location an entrypoint reads or writes —
`.env`, checkpoints, evaluations, logs, caches, inputs produced by earlier experiments — goes
through `code/common/paths.py` `project_root()`/`project_path()`, and every tracked input
(checked-in tables, prompts, fixtures) through `source_path()`. Never derive a storage path from
`Path(__file__)` or `os.getcwd()`.

## 6. Wrap up

- Append the `research-journal` entry (do, then report: show it after writing), then report the
  engineering handoff fields, the elected run identity with its reasoning,
  checkpoint/smoke/WandB decisions, and expected artifacts to the user.
