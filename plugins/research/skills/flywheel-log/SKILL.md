---
name: flywheel-log
description: Publish completed experiments or scientific insights to Flywheel with self-contained evidence, reproducibility artifacts, canonical root resolution, graph topology, and local index updates. Use when the user asks to log, track, publish, or write up research in Flywheel; do not use for incomplete work or planning-only graph authoring.
---

# Flywheel Logging Workflow

## Scope and Source of Truth

This skill is the **sole source of truth** for Flywheel logging: node completeness,
root resolution, node semantics, tags, graph topology, self-contained writing,
artifact requirements and order, publication policy, failure handling, curation,
and `index.md` updates. Global agent instructions decide when logging is required
and who must perform it; they intentionally do not duplicate this procedure.

If another instruction surface disagrees with this skill about how to log, follow
this skill and update the stale surface.

Support both installed Flywheel modes. Use the MCP tools named below when MCP is
available; in CLI mode, load `$flywheel`'s CLI tool map and use the equivalent
`flywheel` command. Never invent a field or command when the active contract is
unclear.

In a Research 2.0 project, require the scientific disposition plus the engineering return packet.
Read execution facts from `EXPERIMENTS.md` and provenance-valid artifacts, but never edit
`EXPERIMENTS.md`, `JOURNAL.md`, `program.md`, or the decision register from this skill. Only
`index.md` is a local logging side effect. Scientific-manual mode requires publication approval;
scientific-auto may publish only to the root already approved in the execution agreement.

## Writing Style (mandatory)

Every Markdown body you write into Flywheel — `summary.md`, the insight node body,
any descriptive text inside a node — must be written in **English** and follow
`references/writing-style.md`. That bundled reference completely defines the
Palette-B, TL;DR, callout, typography, and section conventions; no external style
skill is required.

The non-negotiable parts:
- One `> [!summary] TL;DR` callout immediately after every H1 (**hard cap: 4 lines,
  3 preferred**). This is the **graph-level context preview** a reader sees before
  opening the node — its only job is to convey *what this node is about* well enough
  that it can be told apart from its siblings. Write it as plain high-level prose:
  - **No internal indices or short codes** — never `L0`, `T2`, `T6`, `ckpt_42`,
    `ckpt_2`, `comp_7`, `c_aug`, `K=5`, run IDs, layer/timestep/checkpoint numbers,
    or any token a reader would have to open the node to decode.
  - **No project-internal jargon or abbreviations** that aren't immediately
    understandable from outside the node. Spell things out (e.g. "early layer of
    the SAE" rather than `L0`; "mid-noise timestep" rather than `T2`).
  - Detailed numbers, indices, checkpoint identifiers, and sweep coordinates
    belong in `## Setup` / `## Result`, not in the TL;DR.
  - The TL;DR names the **method / question / qualitative outcome** in human terms;
    precise measurements live in the body.
- Only the Palette B colors (`#540B0E`, `#335C67`, `#E09F3E`, `#9E2A2B`, `#FFF3B0`).
  Map: `#335C67` = win, `#9E2A2B` = regression, `#E09F3E` = mixed. Use color spans
  only in judgemental table cells.
- Only the callout vocabulary listed in the reference
  (`[!info]`/`[!success]`/`[!abstract]`/`[!important]`/`[!warning]`/`[!danger]`/`[!tip]`/`[!failure]`/`[!hint]`).
  No invented callout types.
- Section rhythm: empirical → Setup · Result · Findings · Caveats · Next Steps · Repro.
  Insight → Claim · Evidence · Implication. Horizontal `---` rules between sections.
- Tone: declarative lab record, honest about negatives. Not promotional.

Read the reference once at the start of any logging session, then follow it without
re-reading. If you find yourself reaching for a colour or callout that isn't listed,
stop and use plain prose instead.

## Self-Contained Node Standard (mandatory)

A Flywheel node body must be a self-contained research record, not an index into
local files, Linear tickets, chat history, notebooks, dashboards, or any external
app. Artifacts and run metadata are for audit and reproduction; they must never be
required to understand what was done, why it was done, how it was done, or what the
result means.

Do not write Flywheel-facing prose that depends on references like "see the local
file", "see Linear", "see the dashboard", "see the notebook", "see the Google Doc",
or "details are in the app". If an external system, issue tracker, dashboard,
notebook, document, or local file contains relevant context, copy or summarize the
necessary content inside the node body or uploaded artifacts. The node must carry
the research trace itself: assumptions, inputs, commands, configuration changes,
important log/output excerpts, failure traces, metric definitions, results,
interpretation, and next-step rationale.

For every empirical node, the node body itself must explicitly state:
- **Claim and hypothesis / question**: the precise claim or failure mode being tested,
  the expected direction or outcome, and why this is the right test.
- **Decision criterion**: the metric, threshold, comparison, or qualitative evidence that
  decides whether the claim is supported, falsified, or still unresolved.
- **Setup**: model family, dataset or sample source, conditions, checkpoints,
  layers/timesteps/cells, hardware class, run scale, and any selection criteria.
- **Inputs**: what artifacts/data/checkpoints/features were consumed and how they
  were filtered or sampled. Local paths may appear in Repro, but the body must
  describe the inputs in words.
- **Procedure**: step-by-step experimental method detailed enough that a reader can
  reconstruct the design without opening code or chat history.
- **Controls and baselines**: what each baseline/control changes and why it is a
  valid comparison.
- **Metric definitions**: what each reported metric measures, its direction
  (higher/lower/better/worse), normalization, denominator, and any important range.
- **Results**: counts, coverage, headline values, and key subgroup/status counts.
- **Interpretation**: what the result supports, what it does not support, and how it
  connects to the parent/prior nodes.
- **Caveats and next steps**: limitations that materially constrain the claim.

For every insight node, the body must explicitly state the claim, the evidence nodes
or artifacts supporting it, the reasoning chain, caveats, and the implication for
the next graph branch.

Do not log empirical work as if it were a free-floating run. If the run handoff does
not make the claim, hypothesis, decision criterion, and decisive metric/evidence clear,
reconstruct them from the run plan and artifacts; if they still remain ambiguous, stop
and ask before creating or committing the node.

Before committing a node, run this check: **could a collaborator understand the
experiment and its conclusion without the transcript, local filesystem, Linear,
external apps, or prior private context?** If not, rewrite the node body before
upload/commit. Avoid phrases like "see local file/path for details" as a substitute
for setup or method; use repository-relative paths only when they are part of a
copy-pasteable reproduction command or an uploaded artifact manifest, never as the
only place where the method or result is described.

---

## Step 0 — Resolve the Project Root (do this first, every time)

Before picking a parent, branching a node, or applying a tag, run the canonical
[Research Root Gate](../flywheel/references/research-root-gate.md). It owns lookup
order, ancestry verification, mismatch behavior, and the new-node branching rule.
Do not duplicate or weaken those rules here.

Logging-specific root rule:

- Tags are scoped per root: a re-parent across roots invalidates the previous
  tag IDs. After moving a node, re-apply tags from the **new** root's tag list.

---

## Step 1 — Decide the Semantic Record Type

- **Empirical** → a measured run with a hypothesis, metrics, and concrete outputs.
  Tag: `experiment`.
- **Insight** → framing, interpretation, comparison rule, or next-step decision that
  synthesizes across one or more experiments. Tag: `insight`.

These are content-and-tag semantics, not typed Flywheel body fields. The canonical
node body fields are only `title`, Markdown `content`, and optional `summary`. Put a
stable `Record type: empirical|insight` label and, for empirical work, the hypothesis,
decision criterion, and eventual `Outcome: completed|failed|canceled` in `content`.
Never send removed fields such as `kind`, `node_type`, `hypothesis`, `insights`, or
`no_artifacts_reason` in a node write payload.

If you are tempted to create an insight node that contains measured results, that's a
sign it should be an empirical node instead. Measured results belong inside the
experiment that produced them, not in a separate interpretation layer.

## Step 2 — Build and Persist the Initial Node

First run the [Research Root Gate](../flywheel/references/research-root-gate.md)
for the chosen parent and every evidence node. Build a local payload containing only:

- `title`: concise and role-prefixed, for example
  `E03 Block Activations Beat Uniform at Equal Budget` or
  `I04 Why Uniform Activations Are Inadequate as Baseline`;
- `content`: the self-contained Markdown record, including explicitly labeled record
  type, claim, hypothesis or reasoning, decision criterion, status/outcome, and the
  required sections;
- optional `summary`: one concise synopsis consistent with the TL;DR.

In a Research 2.0 project, every new record must branch from the canonical root or
another ancestry-verified parent. Read that parent to obtain its current revision and
create the child with `flywheel_branch_node` using `expected_revision` (or
`flywheel nodes:branch` in CLI mode). A new unattached record would create another
root and is forbidden. Outside Research 2.0, `flywheel_commit_new_node` (or
`flywheel nodes:commit-new`) is allowed only for a genuinely standalone node; using
it to create a new project root is a protected action requiring explicit approval.
Handle a revision conflict by rereading and reconciling; never blind-retry stale
content. Fetch the persisted node and retain its current committed revision.

Then assign exactly one primary tag with `flywheel_set_node_tag_assignments` using the
current expected revision. Reuse the root-scoped tag definition; create it only when
missing and authorized. Use the canonical Palette B definition:

| Tag | `bg_color` | `text_color` | `one_only` | Use |
|---|---|---|:---:|---|
| `root` | `#540B0E` | `#FFF3B0` | `true` | Stable project root only |
| `insight` | `#335C67` | `#FFF3B0` | `false` | Insight nodes |
| `experiment` | `#E09F3E` | `#540B0E` | `false` | Empirical nodes |

Do not introduce another primary tag or color without explicit user approval.

## Step 3 — Collect Artifacts Locally

For an empirical node, verify the standard artifact taxonomy provides:

- [ ] `summary.md` — structured per `references/writing-style.md`: H1, mandatory
      `> [!summary] TL;DR` callout (≤4 lines), then Setup / Result / Findings /
      Caveats / Next Steps / Repro. The body must state the claim, hypothesis, and
      decision criterion before presenting the result. Do not fall back to a flat
      "setting, inputs, command" dump; follow the bundled Flywheel section rhythm.
- [ ] `reproducibility.md` (template bundled at `assets/reproducibility_template.md`)
- [ ] `commit.txt` — plain text, just `SHA`, `branch`, `repo URL`

For a completed empirical run, also require at least one real plot/image and
machine-readable metrics (`.json`, `.csv`, or `.tsv`). For a failed or canceled run,
require a failure trace or relevant log excerpt plus the summary, reproducibility
attempt, and commit provenance; include plots and metrics only when the run actually
produced evidence for them. Never fabricate a plot, metric, or successful outcome to
satisfy a template.

For every newly Codex-authored plot governed by the research plot communication gate, verify before
upload that the figure visibly and legibly matches the user-accepted specification: exact title,
metric meaning and directional interpretation, and in-figure placement. If the plot omits or changes
that text, or the producing workflow cannot establish explicit user acceptance, stop and route the
correction to the owning plotting workflow; never invent retrospective approval. Do not apply this
check retroactively to extracted source figures, user-supplied plots, or historical external
artifacts.

For an insight node, require only self-contained `content` with the mandatory TL;DR,
claim, evidence-node references, reasoning chain, caveats, and implication. Artifacts,
repository provenance, `summary.md`, `reproducibility.md`, and `commit.txt` are
optional and should appear only when they materially support the insight. A coherent
graph synthesis may be published with no artifacts.

Keep evaluation/metrics and summaries under the run's elected path in `evaluations/`, visual
evidence under `plots/`, checkpoints under `checkpoints/`, and logs under `logs/`. Never create a
generic `outputs/` or Flywheel-only run tree. If any required item is missing, **stop and route its
generation to the owning research skill first**. “Required” is evaluated against the
outcome-specific minimum above. An incomplete empirical
node is worse than no node at all, because it pollutes the graph with claims that
nobody can verify later.

## Step 4 — Write `reproducibility.md`

For empirical work, copy `assets/reproducibility_template.md` beside the run's curated
evaluation summary and fill every section. The file must be self-contained — a
collaborator should be able to re-run the experiment without asking a clarifying
question. For an insight, create this artifact only when the insight itself describes
a reproducible computational procedure.

Keep the template; don't re-derive it inline. If a section genuinely does not apply
(e.g. a CPU-only run has no SLURM config), write `N/A — <reason>` rather than deleting
the section, so the structure stays comparable across runs.

## Step 5 — Upload Artifacts in Order

For completed empirical work, upload visual evidence first (so the node is inspectable
immediately), then `summary.md`, then machine-readable metrics, then auxiliary text files, then
`reproducibility.md`, and finally `commit.txt` so the git SHA is findable at the
bottom of the list. For failed or canceled work, upload the failure trace or log
excerpt first, followed by the summary, any produced metrics or plots,
`reproducibility.md`, and `commit.txt`. For an insight, upload only applicable
supporting evidence; skip the upload lifecycle entirely when the insight is
artifact-free.

Publish curated evidence and summaries, not raw training state. Do not upload
checkpoints unless a repository-local override explicitly requires it. If the run
directory contains large or messy byproducts, upload only the subset needed to
understand, audit, and reproduce the claim.

Use `flywheel_prepare_artifact_uploads` → actual upload → `flywheel_finalize_artifact_uploads`.

## Step 6 — Commit the Coherent Final Snapshot

For empirical nodes, record `Outcome: completed`, `Outcome: failed`, or
`Outcome: canceled` in `content`. Do not silently skip failed runs: a preserved
failure is more valuable than a run that quietly disappears.

After artifact finalization, or immediately after initial persistence for an
artifact-free insight, reread the node, acquire a stage lease with
`flywheel_acquire_stage_lease`, and build the complete final `staged_payload` from the
latest durable state. It must contain the canonical `title`, `content`, and optional
`summary`, including the final outcome and artifact manifest or links. Commit with
`flywheel_commit_node` using the returned `stage_session_id`, the latest
`base_committed_revision`, and that full `staged_payload`; then release the lease.
Heartbeat the lease during a long edit. In CLI mode, use the equivalent
`nodes:get` -> `nodes:stage:lease:acquire` -> `nodes:commit` ->
`nodes:stage:lease:release` sequence. A conflict requires a fresh read and explicit
reconciliation, not transport retry.

## Step 7 — Place the Node in the Graph

- Extend the current causal chain rather than starting a new branch, unless the
  question is genuinely parallel.
- Place insight nodes between experiments when interpretation or next-step framing
  would help the next reader.
- Never place every experiment directly under the root — that flattens the story into
  noise.

## Step 8 — Update the Local Index

Prepend the new node entry to `index.md`, or deterministically regenerate and sort
the entries newest first, so future sessions read a current local mirror before
listing Flywheel nodes. One block per node:

- H2 title matching the node title
- a metadata line: `` `<node-id>` · <kind> · <outcome> · <date> ``
- the verbatim TL;DR from the node's canonical `content`

If `index.md` does not exist, create it with an H1 (`# Flywheel Node Index — <project>`)
and a one-line note that the authoritative record lives in Flywheel.

## Step 9 — Maintain Graph Curation

During an active research loop, create or propose a non-heartbeat automation that
opens a separate task every 10 hours to audit the relevant Flywheel graph. It
should check titles, TL;DRs, summaries, tags, parent/child placement, topology, and
`index.md` consistency.

The curation task may correct wording, missing tags, stale index entries, or other
readability defects only when authorized by scientific mode and the change preserves the scientific claim, outcome,
evidence, lineage, and parentage. If any of those would change, produce a concise
review note and ask the user before mutating the graph.

---

## Error Handling

If something goes wrong partway through, fail loudly rather than leaving a half-finished
node in the graph:

- **Artifact upload fails** → do not finalize an incomplete prepared batch. Retry the
  missing raw-byte uploads under the active batch contract, or abandon that batch and
  prepare a new one containing the authorized outcome-specific artifact set. If the
  active contract does not explain recovery, inspect it and stop rather than guessing.
  Never call a failed upload successful. Completed empirical work still requires its
  plot, metrics, and `commit.txt`; failed/canceled work requires its failure evidence
  and `commit.txt`.
- **`flywheel_commit_node` fails** → inspect the latest durable node and stage-lease
  state with `flywheel_get_node`. On a revision conflict, reacquire or refresh the lease,
  reconcile the full payload against the new revision, and commit deliberately. Release
  the lease when safe. If unrecoverable, report the incomplete record and request
  explicit approval before deletion; graph deletion is always protected.
- **Wrong tag applied** → remove the old tag and apply the correct one. Tags are cheap
  and the color convention is load-bearing for graph readability.
- **Wrong parent** → use `flywheel_remove_parent` + `flywheel_add_parent` rather than
  deleting and recreating the node; the node ID is worth preserving if the content is
  correct.

---

## Examples

### Empirical node — completed run

```
title:       E07 SiLU² Block Beats SiLU at Equal Time Budget
content:     Record type: empirical
             Hypothesis: At a fixed 90-minute training budget, a block schedule
             with SiLU² activations yields lower validation loss than the same
             schedule with plain SiLU.
             Outcome: completed
parent:      E06  (previous block-schedule baseline)

Artifacts uploaded in order:
  1. val_loss_curve.png
  2. activation_ablation_bars.png
  3. summary.md
  4. metrics.json
  5. metrics.csv
  6. reproducibility.md
  7. commit.txt
```

Corresponding `summary.md` (bundled Flywheel style — note the TL;DR, colour spans,
and section rhythm; full reference in `references/writing-style.md`):

````md
# E07 SiLU² Block Beats SiLU at Equal Time Budget

> [!summary] TL;DR
> Compares **SiLU² block** against a plain-SiLU block schedule at a fixed
> training-time budget. SiLU² wins consistently across the budget sweep, but the
> gain ==saturates once the budget is large enough== — extra compute stops paying
> off. Single seed; multi-seed confirmation pending.

---

## Setup

> [!info] Constraints
> - **Dataset**: ImageNet-32 train split, 1.28M samples
> - **Hardware**: 1× `<accelerator_model>` on `<provider_or_host>`
> - **Budget**: 90 minutes wall-clock, seed 42
> - **Metric**: val loss after 90 min, lower is better

---

## Result

| Schedule | Val loss @ 90min | Δ vs. baseline |
|---|:---:|:---:|
| ==SiLU (baseline)== | <span style="color:#9E2A2B">**1.327**</span> | reference |
| **SiLU² block** | <span style="color:#335C67">**1.284**</span> | <span style="color:#335C67">**−0.043**</span> |

![[val_loss_curve.png]]

> [!important] Headline
> SiLU² block dominates plain SiLU across every checked budget.

---

## Findings

### 1. Gain is monotone in budget, then plateaus

![[activation_ablation_bars.png]]

> [!abstract] Budget sensitivity
> Δ at 30/60/90 min = −0.029 / −0.038 / −0.043; at 120 min Δ collapses to −0.044.

### 2. No detectable optimiser instability

> [!info] Diagnostics
> Grad-norm and update-norm trajectories overlap baseline (see `metrics.json`).

---

## Caveats

> [!warning] Single seed
> One seed per condition — variance not characterised; budget for ≥3 seeds before
> promoting SiLU² block to a permanent default.

---

## Next Steps

- [ ] Sweep SiLU² block across ImageNet-32 / ImageNet-128 / CIFAR-10
- [ ] Re-run with seeds 1, 2, 3 to characterise variance
- [x] ~~Check whether gain depends on warmup length~~ — no (Δ stable for warmup ∈ {500, 1000, 2000})

---

## Repro

See `reproducibility.md` · commit `<sha>` on `main`.
````

### Insight node — synthesis across experiments

````md
# I05 Treat SiLU² Block as New SlowRun Candidate

> [!summary] TL;DR
> Argues that **SiLU² block** should replace plain SiLU as the default SlowRun
> activation. The case rests on the budget-matched comparison in [[E07]] and is
> contingent on a multi-seed re-run before being treated as load-bearing.

---

## Claim

> [!important] Promote SiLU² block to default
> Replace plain SiLU with SiLU² block in the SlowRun config; keep SiLU available
> as an opt-in baseline for ablation studies.

---

## Evidence

- [[E07]] — SiLU² block beats SiLU at 30/60/90 min, plateau beyond 90 min (single seed)
- [[E06]] — block schedule (any activation) already beats uniform under same budget;
  this insight strengthens that conclusion rather than replacing it

---

## Implication

The next empirical step is a **dataset-scale sweep** so the gain is not pinned to
ImageNet-32. After that, a 3-seed re-run is needed before this insight can be
treated as load-bearing for downstream experiments.

> [!tip] Next experiment
> Open `E08 SiLU² Block × Dataset Scale (3-seed)` — width 0, two parallel
> branches: ImageNet-32 (sanity) and ImageNet-128 (target). Budget 90 min/run.
````
