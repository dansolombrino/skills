# Research-project conventions (shared canon)

The single source of truth for the conventions all `research` bundle skills enforce.

During initial scaffolding, place `.gitkeep` in every otherwise-empty taxonomy
directory so Git preserves the complete tree. Remove a placeholder only after the
directory contains another tracked file.

## Research 2.0 admission and modes

Support only repositories scaffolded with the Research 2.0 surfaces in the taxonomy below. If a
required surface is missing, or legacy status/scripts are detected, stop as unsupported. Do not
offer compatibility, migration, or opportunistic upgrades.

Read `program/00-execution-agreement.md` before a material scientific or engineering action. It
must explicitly set `scientific_mode: manual|auto` and `engineering_mode: manual|auto`; never infer
either. Manual mode reserves that layer's choices for the user. Auto mode delegates that layer's
choices only inside its approved scientific scope or engineering envelope. Integrity gates never
become optional.

Always return to the user for destructive operations, history rewrites, deletion of material data,
overwriting user changes, new repositories/remotes/accounts/destinations, secrets/authentication
changes, scope/budget/rig expansion, shared-GPU exceptions, ambiguous Flywheel roots, or a
source-to-target deviation governed by `integrate-reference-code`. System permission prompts are
independent and always apply.

## Directives are closed

- **The active skill's `SKILL.md`, its `references/`, and its bundled templates are the complete
  and only specification** for structure, naming, file contents, and defaults. Follow them
  literally. They are not a starting point to be enriched from elsewhere.
- **Never list, search, read, or copy from another project, repository, or directory on disk** —
  sibling projects, prior research checkouts, home-directory clones — to infer conventions, file
  contents, defaults, or how something was done last time. An existing project found on disk is
  not a template, a precedent, or an example, however similar it looks.
- **When a directive is missing or ambiguous, stop and ask the user.** Never close the gap by
  imitation and never silently default. A convention the skill does not state is a question for
  the user, not something to go discover.
- **Never carry secrets or credentials across projects.** Do not copy or commit a token from
  another project's `.env` or from any other machine-local secret source.

Reading outside the target project is legitimate in exactly these cases, and each is already
named by a directive rather than discovered by looking around:

- the target project itself, including its own history and tracked files;
- the installed directory of a `research` skill — the folder holding the SKILL.md being followed,
  with its `assets/`, `scripts/`, and `references/` — which is how `assets/environment.py` reaches
  `code/common/environment.py`;
- literal values written into the templates, which are reproduced verbatim because a directive
  puts them there, not because another project was inspected;
- the user's machine registry (`~/.config/rigsync/machines.toml`), which is where each rig's
  volumes and shared caches are declared. Read the declarations; never substitute a value observed
  on a rig or copied from another project for a missing one;
- material the user placed in the project's own `references/` directory, or a reference the user
  identifies explicitly by path or URL, governed by `integrate-reference-code`. References are
  always user-supplied; never go looking for candidates on the filesystem.

## Taxonomy

```
[project root]
├── checkpoints/     # checkpoints from trainings/finetunings
├── code/            # experiment code (hydra-configured python)
│   └── common/
│       ├── environment.py # stable installed-environment fingerprint
│       ├── run_id.py      # shared run identity + collision guard
│       └── status.py      # atomic run lifecycle + progress signaling
├── config/          # hydra yaml configs for code/
├── evaluations/     # data produced by running experiments (+ .status.json markers)
├── logs/            # run logs (tee'd stdout+stderr of each run, wandb dirs); mirrors the scripts/ tree exactly; gitignored, dir tracked, rig-local, never synced
├── plots/           # plots produced by visualizations/ (one subfolder per plotting script)
├── scripts/         # shell scripts that LAUNCH things (shell only — never yaml); wave-scoped, see "Waves and GPU lanes"
├── visualizations/  # plotting code (argparse python), sibling of code/
├── shitpads/        # temp space; gitignored, dir tracked, rig-local, never synced
├── references/      # papers/codebases to reference; gitignored, dir tracked, rig-synced
├── program/         # tracked scientific agreement, decisions, and phase reports
├── orchestration/   # local-only append-only control/subagent traces; gitignored
├── .env             # secrets + machine-varying paths ONLY (gitignored)
├── .env.example     # committed mirror of .env keys with placeholders
├── .python-version  # exact Python patch used on every rig
├── pyproject.toml   # dependencies + exact required uv version
├── uv.lock          # exact cross-rig dependency resolution
├── sync.toml        # Git/artifact/machine paths + environment smoke contract
├── README.md        # static scaffold (structure, setup, how-to)
├── AGENTS.md        # thin: project specifics + light awareness map (canonical project notes)
├── CLAUDE.md        # thin pointer to AGENTS.md so Claude Code loads the same notes
├── program.md       # one-screen current scientific index
├── index.md         # local mirror of the authoritative Flywheel graph
├── JOURNAL.md       # story: prose log of what/why/learned (append-only)
└── EXPERIMENTS.md   # state: run tracking tables
```

Run-log-producing tools must write into `logs/` — e.g. `wandb.init(dir=<project_root>/"logs")` —
never the project root.

Every `scripts/` wave script captures its own stdout/stderr: the python invocation is piped
through `tee` into the **mirror of its own path under `logs/`** — same tree, `logs/` as the
leading folder, `-<YYYYmmdd-HHMMSS>` appended to the filename:

```
scripts/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>.sh
logs/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-<YYYYmmdd-HHMMSS>.log
```

So `logs/` mirrors `scripts/` (flat run_id form, wave-scoped) — you reach a log from its
script — while `checkpoints/`, `evaluations/`, and `plots/` all use the nested run_id **path**
form after their experiment-specific prefixes and stay run-scoped. Logs are timestamped per launch,
so history is kept; logs are never overwritten, and a crash-recovery relaunch never clobbers
the earlier attempt. Hydra projects must disable hydra's job file logging (defaults entry
`- override hydra/job_logging: none`) or explicitly point it inside `logs/` — a stray
`<job_name>.log` must never appear in the project root.

Experiments are named `NNN_[experiment_name]` (three-digit zero-padded), optionally nested
(`NNN_exp/NNN_sub_exp/...`). The same `NNN_...` hierarchy is mirrored across
`checkpoints/ code/ config/ evaluations/ logs/ plots/ scripts/ visualizations/`.
Call this complete numbered leaf hierarchy `<experiment_path>`; never shorten it to the parent
experiment when the producer is a sub-experiment. A visualization with exactly one producer lives
at `visualizations/<experiment_path>/<script_stem>.py` and writes only below
`plots/<experiment_path>/<script_stem>/<partial_run_id path>/`. For example:

```
visualizations/000_grokking/plot_loss.py
plots/000_grokking/plot_loss/model=mlp/lr=1e-3/seed=0/loss_curve.pdf

visualizations/001_compression/002_weight_error/plot_layers.py
plots/001_compression/002_weight_error/plot_layers/model=vit/seed=0/layer_error.pdf
```

Do not infer placement for a visualization that combines multiple producer experiment paths; stop
and resolve that separate taxonomy decision with the user.

## run_id

The per-experiment **ordered** set of config params that uniquely identifies a run.

- **Election owner follows `engineering_mode`.** In manual mode, propose and wait for the user.
  In auto mode, elect and record the smallest ordered identity that prevents collisions inside the
  approved engineering envelope.
- Authoritative record: a constant in the experiment's `.py` (e.g. `RUN_ID_PARAMS`) that the
  path-building code actually uses; mirrored in the experiment's EXPERIMENTS.md section header.
- Config yamls stay pure hydra params — **no run_id metadata in yaml**.
- Two renderings via `code/common/run_id.py`:
  - **path form** — nested subfolders, e.g. `model=mlp/lr=1e-3/seed=0/` → used under
    `checkpoints/`, `evaluations/`, `plots/`. `param=value` segments are the default; keys and
    values are percent-encoded by the shared helper so `/`, spaces, commas, shell metacharacters,
    and structured values cannot alter the hierarchy. Any shortening (e.g. bare `mlp/`) is a
    per-experiment deviation decided by the applicable engineering-mode owner.
  - **flat form** — e.g. `model=mlp,lr=1e-3,seed=0` → uses the same percent-encoded components
    and is used for `scripts/` and `logs/`
    run-folder names, wandb run names, and log lines. Flat name ≡ wandb run, 1:1; it maps to
    one EXPERIMENTS.md row **per wave** the run took part in (see below).

### Plot communication approval

Before every creation or modification of code under `visualizations/`, read the producer's
authoritative ordered `RUN_ID_PARAMS` and metric definitions, then propose one exact communication
specification for every plot the code produces:

- the title template, including every fixed RUN_ID param as `key={value}` in elected order;
- visible in-figure text explaining what every plotted metric measures and whether higher, lower,
  a target/range, or no universal direction is preferable; and
- the exact placement of that text in an axis label, subtitle, legend, annotation, or in-figure
  caption.

Omit aggregated RUN_ID params and describe aggregation semantically only when useful. When no
RUN_ID param is fixed, propose a semantic title with no fabricated RUN_ID part and state that fact.
Cover multiple axes, panels, derived metrics, and visual encodings separately unless one shared
explanation is unambiguous. Stop rather than guess when metric semantics cannot be grounded in the
producer, evaluation schema, or scientific contract. External or historical figures that the
workflow did not author are not retroactively subject to this gate.

The agent proposes the exact wording, punctuation, formatting, and placement, but only the user may
accept them. Wait for explicit user approval before the code edit. This is always protected:
scientific-auto and engineering-auto cannot approve it, and a prior approval does not carry across
a later plotting-code edit even when the proposed specification remains unchanged. Repeated renders
of already-approved, unchanged code need no new approval. After rendering, verify that the accepted
text is present, legible, and unchanged inside the figure; external prose alone is insufficient.

### Plot execution

Treat plotting as a direct, non-orchestrated fast path rather than an experiment launch. Run each
plotting script's argparse command in the foreground from the project checkout on `rig-4090`, where
evaluations converge. If another host is active, connect directly to `rig-4090`; stop when its
checkout cannot be identified or reached.

Do not invoke `scientific-orchestrator`, `sweep-dispatch`, `rig-sync`, or
`experiments-tracking` for plotting. Do not create orchestration control state, mint a wave id,
generate a launch script, dispatch work, start tmux, or add/update `EXPERIMENTS.md` rows. Plot
title approval and the applicable engineering-mode decisions remain in force; this fast path only
removes experiment-launch machinery from plotting.

### run_id schema evolution

A run_id election is only valid for the config schema it was elected against. Any change that
**adds, removes, or renames a behavior-affecting config param** (integration, refactor, new
feature) invalidates the current election ⇒ **mandatory re-election before any new run launches**.
In engineering-manual mode the user decides; in engineering-auto mode the agent decides inside the
approved envelope and records its reasoning. A param may stay out only when the applicable owner
rules it non-identifying (e.g. a pure logging knob). Otherwise, new runs varying the new param
would collide with old artifacts at the same `<run_id path>` — silently overwriting them, or
worse, being skipped by an artifact-guarded wave script as "already done".

- **If the param joins `RUN_ID_PARAMS`** — a migration decision is required for existing
  artifacts. Default proposal: **backfill-rename** — insert the new `param=<old implicit
  value>` segment at its elected position in existing dirs under
  `checkpoints/ evaluations/ plots/` (path form) and `logs/ scripts/` (flat form), and add the
  column to EXPERIMENTS.md rows
  (wandb run names cannot be backfilled — note the schema change in JOURNAL.md instead).
  Alternative: freeze the old tree and start a new sub-experiment for the new code path. The
  applicable engineering-mode owner chooses and records the option.
- **Runtime guard (canon)** — every run writes its **full resolved config snapshot** to
  `evaluations/NNN_exp/<run_id path>/.run_config.json` via `guard_run_config` in
  `code/common/run_id.py`, called before any artifact is written. If a snapshot already exists
  and differs on any param while the run_id is identical ⇒ **hard error** naming the differing
  params ("run_id collision — re-elect run_id or migrate"); never proceed. Same-config reruns
  (resume/retry) pass.
- Corollary: `guard_run_config` catches collisions only once Python starts. An artifact-guarded
  skip does not enter Python, so the mandatory sweep coverage gate must reject schema drift
  before scripts are generated. Never describe the runtime guard as validating a skipped run.

## Waves and GPU lanes

A **wave** is one dispatch decision: the set of runs launched together, however it is split
across machines. A **slice** is the portion of a wave assigned to one rig; a **lane** is the
portion of that slice assigned to one GPU set. A run_id's grid is rarely launched in one go — you launch a subset now and
more later, re-launch what failed, re-run after a code fix, move work to a freer rig — and each
of those is its own wave. Waves are the unit of launch, of on-disk execution history, and of
EXPERIMENTS.md rows.

- **wave id** — `YYYYMMDD-HHMMSS` (e.g. `20260731-162043`). Minted **once per dispatch**, on
  rig-4090 in local time, at the moment the assignment is authorized under the active mode (the id is in the paths, so
  it must exist before generation). **Shared by every rig and lane in that dispatch** — one
  decision, one id — which is what makes `grep -r <wave_id> scripts/` return the whole dispatch.
- **Reused verbatim on crash recovery.** A post-reboot relaunch is not a new dispatch: same id,
  same paths, same tmux session names. A new id is minted only for a new dispatch decision.
- The id is a **pure timestamp — never a semantic slug.** It is replicated into `.status.json`,
  EXPERIMENTS.md rows, tmux session names, annotated Git tag `wave--<wave_id>`, and one script
  path per run. Human meaning goes in the wave `README.md`, written once at generation.

Two axes say where a run executed: the **rig** and the **GPU set** on it. A **lane** is the
portion of a wave assigned to one GPU set on one rig.

- The GPU field is **opaque identity, not semantics**: record which card(s) a run occupied,
  never reason about *why* it uses more than one (DDP, sharding, anything else is the
  experiment code's business).
- Written `gpu<ids>` — `gpu0`, or `gpu0,1,2,3` for a multi-GPU lane (comma style as in
  `run_id_flat`). **Always present, including on single-GPU rigs** (`wave_rig-4090_gpu0.sh`) —
  one filename shape everywhere, uniform globs.
- Lanes on a rig run **in parallel** (one tmux session each); runs within a lane run
  **sequentially**. Within one (wave, rig) the GPU sets must be **disjoint** — overlapping lanes
  would fight over a card. A run occupying every GPU therefore makes that rig a single lane for
  that wave.

Canonical layout — one self-guarded script kind:

```
scripts/NNN_exp/<run_id_flat>/wave_<wave_id>/
    README.md                     # what this wave is; written once at launch, never updated
    wave_<rig>_gpu<ids>.sh        # this run's invocation in this wave
```

`ls scripts/NNN_exp/<run_id_flat>/` is that run's execution history — which waves it took part
in, on which rig, on which cards. **The filesystem encodes the assignment**: a run is on
rig-4090 GPU 0 in this wave precisely because `wave_rig-4090_gpu0.sh` exists in its wave folder,
so there is no separate manifest to drift. Only `scripts/` and `logs/` are wave-scoped;
`checkpoints/`, `evaluations/` and `plots/` stay **run-scoped in path form** — artifacts must
keep a stable per-run path or both the launcher's artifact guard and `guard_run_config` break.

tmux session name: `<project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>` — e.g.
`grokmod_000_grokking_20260731-162043_behemoth_gpu0`. The project prefix matters
because tmux sessions are global per rig across projects; the wave id lets successive waves
coexist; the GPU field keeps parallel lanes apart. Generation, dispatch and recovery protocol:
`sweep-dispatch`.

### Tested Git revision gate

Every current wave is one immutable Git deployment decision:

- Generate its scripts and approved tracking/JOURNAL changes, stage the intended patch, and run
  the experiment's recorded smoke command on rig-4090. Record the command and pass criterion in
  the experiment's EXPERIMENTS.md section header.
- If the smoke leaves the staged patch and execution worktree unchanged, commit it and create
  annotated tag `wave--<wave_id>` at that commit. Push the configured branch and tag without
  force; both must resolve to the same full SHA on GitHub.
- `rig-sync` may advance assigned rigs only by fast-forward to that SHA. Every rig must expose
  the configured branch/remote, exact `HEAD` and tag, and clean tracked/staged/non-ignored
  execution paths (`code/`, `config/`, `scripts/`, and root dependency manifests).
- Verify the revision across the full rig set before any lane launches, per rig immediately
  before tmux, inside every queued script before Python, and before crash recovery. Source drift
  exits `86`, stops the lane, and is not an experiment failure.
- Never change a rig to a different revision while that project's tmux lane or `running` status
  remains active. Never reset, stash, clean, force-push, or merge non-fast-forward to make a rig
  comply. Machine `.env`, datasets, artifacts, logs, and installed environments remain outside
  the Git consistency claim and require the separate parity gate below.

### Verified environment parity gate

Every wave also carries one immutable user-space environment identity:

- The project commits `pyproject.toml`, `uv.lock`, an exact `.python-version`,
  `code/common/environment.py`, and `[environment]` in `sync.toml`. Use only uv's configured
  default dependency groups; never add per-rig extras or manual packages.
- `environment-sync` installs the exact required uv and Python without `sudo`, asks the user for
  one environment directory name, records it as `[environment].name`, materializes that ignored
  directory on each machine with `uv sync --frozen --exact`, and refuses mutation while project
  lanes or `running` statuses exist.
- The fingerprint hashes the lock, exact interpreter, and canonical installed package
  names/versions. It must match the hub on every assigned rig. OS/architecture must be compatible;
  GPU/driver differences are allowed only when the project smoke passes under the assigned GPU set.
- Verify the full set after revision deployment, per rig immediately before tmux, inside every
  queued script before experiment Python, and before recovery. Environment drift exits `87`,
  stops the lane, and is not an experiment failure. Launch scripts verify only; they never sync.
- Export `ENVIRONMENT_FINGERPRINT` into the run and record it in `.status.json`. Outputs whose
  source or environment provenance differs from the wave are not comparable.
- On a quota'd rig, verify storage headroom before dispatch (`rig-sync doctor`) and again inside
  every queued script before experiment Python. Insufficient storage exits `88`, stops the lane,
  and is not an experiment failure. Gate before launch rather than after: a run that exhausts its
  allowance mid-write leaves a truncated checkpoint that is indistinguishable from a real one
  until it is loaded.

## Rig fleet

**The canonical name is the ssh alias** — dispatch runs `ssh <rig>`, and the name also lands in
script filenames and tmux session names, so it must be the string that actually resolves.

| canonical name | sloppy names to recognize                                           | GPUs     | per-GPU speed weight | storage | role |
|----------------|---------------------------------------------------------------------|----------|----------------------|---------|------|
| `rig-4090`     | 4090                                                                | 1        | 1.0                  | dedicated volume | main/hub: projects start here, dispatch + EXPERIMENTS.md writes happen here |
| `rig-3090-ti`  | 3090, 3090 ti, `rig-3090ti`                                         | 1        | 0.5                  | dedicated volume | support |
| `rig-3080-ti`  | 3080, 3080 ti, `rig-3080ti`                                         | 1        | 0.5                  | dedicated volume | support |
| `behemoth`     | pro 6000, 6000, bw, blackwell, `rig-6000-pro-blackwell`, `server-pro-6000-bw` | 8 (only GPU 0 is ours) | 2.0 | **quota'd, home is small** | support |

The storage column is a property of the rig, not of a project. Its concrete values — which volume
is the large one, where the storage root is, where the shared caches sit — live in the user's
machine registry (`rig-sync` → `references/configuration.md`), never in a skill or a committed
project file.

That registry is written once per machine and reused by every project on it, which makes it a
**prerequisite for scaffolding** rather than a step inside one: a rig with no entry cannot be
declared in a project's `sync.toml`, and its shared caches cannot be installed by
`rig-sync provision-env`. A project scaffolded past that gap runs with no `HF_HOME` or
`UV_CACHE_DIR` and falls back to `~/.cache` — on `behemoth` that is the small quota'd volume.

### behemoth is GPU-0-only

`behemoth` is a **shared machine**. **GPU 0 is the only card that belongs to us.** Every other
card is assigned to other people and is off-limits — an idle card is not an available card.

The agent never proposes, generates, or launches on any behemoth GPU other than 0.

The **only** exception: the user explicitly states they have obtained authorization for
specific additional cards. That grant originates with the user and nowhere else — the agent never
infers it from an idle `nvidia-smi`, never assumes it, never fishes for it with a leading
question.

A grant is **per-wave and never persisted**: no `.env` var, no config file. It covers the one
dispatch it was given for; the next wave defaults back to GPU 0 and must be re-granted. Its
only record is the `# GPU auth:` header baked into that wave's scripts (`sweep-dispatch`
templates), which the scripts also enforce at runtime.

The weight is **per GPU**, so a rig's capacity in the assignment math is
`weight × (number of free GPUs)`. Never hardcode the server's card count — it can change, and
the number of GPUs actually free varies per dispatch: **ask the user which rigs AND which GPUs
are free** before proposing an assignment (`sweep-dispatch`, pre-launch gates). For `behemoth`
that math defaults to `2.0 × 1` (gpu0), and only a grant for this wave widens it.

### behemoth is quota'd

A shared machine shares its **disk** the same way it shares its cards, and `behemoth` enforces
**per-user disk quotas** on more than one filesystem. The home volume is the small one; the
large volume is a separate mount with its own, much larger quota.

A rig's storage root is **declared, never inferred**. The agent never defaults a `repo_path`, a
cache directory, a `TMPDIR`, or an artifact destination to `$HOME` or `~/.cache` on a quota'd rig.
Where the large volume is mounted is a fact that belongs in the machine registry; a skill that
guesses it is wrong on the next rig.

**A quota is not free space.** `df` reports the filesystem, not the user's allowance: it can show
terabytes available on a volume where the next write fails with `Disk quota exceeded`. Checking
disk headroom on a quota'd rig means asking the quota system (`quota`), not `df` alone — and the
two can disagree by orders of magnitude.

Three consequences worth stating explicitly, because each one has bitten:

- **A convenience symlink hides the problem, it does not solve it.** If `~/project` points at the
  large volume, a wrong `repo_path` still *works* — right up until someone recreates the directory
  for real. Resolve paths (`readlink -f`) when verifying, and fix the declaration, not the symlink.
- **Machine-level environment is defeated by project-level `.env`.** A rig can be configured
  perfectly and a single `HF_HOME=` line in a project's `.env` will silently override it. Cache
  paths that vary per machine do not belong in per-project files.
- **Project-scoped storage is repo-relative, not absolute.** `.env` declares `storage/cache`, not
  a mount point, and `code/common/paths.py` resolves it against the project root. Since a rig's
  `repo_path` is already required to sit on its large volume, relative paths inherit that for
  free — and the same `.env` is then correct on every rig, present and future. An absolute
  per-rig path in any per-project file is a defect: right on the machine it was typed on, wrong on
  the next, and repaired by pointing it at `$HOME`.
- **Shell rc files do not reach the jobs that matter.** Dispatch runs under `nohup`, `ssh <cmd>`,
  and generated scripts — none of which are interactive shells. An export that lives only in an
  interactive rc file is absent exactly when a long run needs it. Machine-level environment must
  be set where *every* shell sees it.

Storage exhaustion is a **stop condition, not a warning**: a run that dies partway through leaves
truncated checkpoints that look like real ones. Gate before launching, not after.

Passwordless ssh between all rigs. Git/GitHub distributes launch source; selected artifact
movement is **`rig-sync`'s job**. Run its bundled doctor's checks before dispatch; if they fail,
stop instead of inventing ad-hoc copying. The hub may be the current local host even when its SSH
daemon is not listening: local work uses direct commands while peer work uses bounded SSH.

## Status ground truth — three signals, artifacts golden

Every run leaves three on-disk signals. First require a current wave's status revision/tag to
match its Git tag; a mismatch is an integrity error and its outputs are not comparable. Once
provenance is valid, disagreements resolve as **artifacts win**, then `.status.json`, then log.

1. **Artifacts (golden)** — the run's expected final artifact (final checkpoint under
   `checkpoints/NNN_exp/<run_id path>/` and/or final eval output under
   `evaluations/NNN_exp/<run_id path>/`), declared at experiment design time. Present ⇒ the
   run really finished; absent ⇒ not done, whatever the status file says.
2. **`.status.json`** — `evaluations/NNN_exp/<run_id path>/.status.json`, written by the
   python script itself (via the StatusWriter pattern in
   [templates.md](templates.md#codecommonstatuspy)):

   ```json
   {"schema_version": 2, "state": "running|done|failed", "started": "<ISO>",
    "ended": "<ISO>|null", "elapsed_s": 1234.5, "heartbeat": "<ISO>",
    "progress": "epoch 3/10", "progress_completed": 3, "progress_total": 10,
    "progress_unit": "epoch",
    "wave_id": "20260731-162043", "gpu": "0",
    "source_revision": "<40-char-sha>", "source_tag": "wave--20260731-162043",
    "environment_fingerprint": "<64-char-sha256>"}
   ```

   Single writer during execution: the python script owns it; after a nonzero exit with no final
   artifact, the wave script atomically ensures `failed` as a fallback. One file per run →
   conflict-free by construction;
   deleting a run's outputs resets its status. `wave_id` is what lets reconciliation match the
   file to the right EXPERIMENTS.md row (rows are per (run, wave)); `gpu` rides along for
   awareness. Source fields prove which tested commit executed and must resolve through the wave
   tag during reconciliation. The placement/source fields and environment fingerprint reach
   Python as **environment variables** exported by the wave script (`WAVE_ID`,
   `CUDA_VISIBLE_DEVICES`, `SOURCE_REVISION`, `SOURCE_TAG`, `ENVIRONMENT_FINGERPRINT`), deliberately
   **not** as config params — they must stay
   out of the `guard_run_config` snapshot, or every re-launch in a new wave or on a different
   card would trip the run_id-collision hard error. `StatusWriter` refreshes `heartbeat` and
   live `elapsed_s` atomically every 60 seconds even when a training unit is still running.
   Experiment code reports numeric progress with
   `heartbeat(completed=<n>, total=<n>, unit=<label>)`. ETA is deliberately absent: it is
   derived by the monitoring/tracking layer, never persisted as if it were measured fact.
3. **Run log** — the latest
   `logs/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-<YYYYmmdd-HHMMSS>.log`
   (the mirror of the wave script's own path): the run's **entire stdout+stderr** (tee'd by the
   wave script, terminal-redirection style; timestamped per launch, history kept).
   Tracebacks/errors near the tail ⇒ failed; a stale heartbeat plus a silent log ⇒ suspect a
   hang — after ruling out a machine fault (below).

A `.status.json` frozen at `running` has three readings: **healthy** (heartbeat advancing),
**process hang** (rig up, tmux session alive, heartbeat frozen — report, don't auto-kill), or
**machine fault** (rig unreachable/rebooted — boot time via `uptime -s` postdates the
heartbeat — or the lane's tmux session is gone). A machine-fault run is **interrupted**, not
failed-by-code, and is safe to relaunch: every `wave_<rig>_gpu<ids>.sh` is **self-guarded**
(it skips itself only if its final artifact is present), so
re-issuing a lane's dispatch command never redoes finished work — interrupted runs re-execute,
resuming or restarting per the experiment's design-time resume decision. Detection + recovery
protocol: `sweep-dispatch`.

- **EXPERIMENTS.md is single-writer: only ever edited on rig-4090** — and within a launch
  session, only by the orchestrator chat, never by monitoring subagents.

## Launch-chat reporting contract

The chat that launches a wave remains active until all of that wave's runs are terminal or the
user explicitly asks it to stop monitoring. Before launch, require parallel background subagents plus a
recurring wait/monitor capability; if either is unavailable, stop rather than promise updates
the chat cannot deliver. Never implement the cadence with a blocking shell `sleep`.

- Anchor a fixed ten-minute schedule to the confirmed launch time. At every tick, reconcile
  EXPERIMENTS.md on rig-4090 and emit an update even when nothing changed. Urgent completion,
  failure, hang, rig-down, and recovery messages happen immediately and never reset the fixed
  schedule. When the wave becomes terminal, report immediately and stop; do not wait for a tick.
- Every message opens with `Status written <timestamp> —`, where `<timestamp>` is generated
  immediately before sending as an ISO-compatible local timestamp with seconds and UTC offset.
  Report each status observation's heartbeat age too, so message time and data freshness cannot
  be confused.
- Show done/running/queued/failed counts, every active run's progress and estimated completion,
  each lane's queued count and estimated completion, and the estimated completion of the wave.
  Label estimates and their basis; say `ETA unavailable` when no sound basis exists.
- Treat a schema-v2 heartbeat older than three minutes as stale. Diagnose rig/session/process
  health before trusting its ETA or declaring a hang. Legacy statuses remain readable, but a
  future launch must first upgrade target experiment code to schema-v2 structured telemetry.

## Root-file duties

- EXPERIMENTS.md = **state** (tables, reconciled from the three signals above; includes each
  run's `started`/`ended`/`elapsed` — reference runtimes for estimating future temporal
  budgets). JOURNAL.md = **story** (prose: what, why, what was learned; append-only, never
  rewritten).
- New env var read by code → `.env.example` updated in the SAME turn.
