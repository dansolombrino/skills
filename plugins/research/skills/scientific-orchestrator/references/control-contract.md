# Research 2.0 control contract

## Agreement fields

Keep `program/00-execution-agreement.md` under 200 lines and preserve user-authored values. Give
the agreement a monotonically increasing `agreement_version` and record:

- active request, scientific question, claim, hypothesis, live alternatives;
- evidence stage: exploratory, confirmatory, or final;
- decisive evidence, decision criterion, evaluation boundary, stop rule;
- `scientific_mode: manual|auto` and `engineering_mode: manual|auto`;
- scientific scope and explicitly excluded branches;
- repository, branch, approved remotes, rigs/GPUs, compute/time/money ceilings;
- approved publication and tracking destinations, including canonical Flywheel root id/title;
- hard constraints, soft preferences, delegable fields, and protected choices;
- agreement owner, approval state, and latest material revision.

Never infer either mode. Blank or ambiguous protected fields block the dependent action.

## Four combinations

| Scientific | Engineering | Behavior |
|---|---|---|
| manual | manual | User approves research branches and concrete technical realization. |
| manual | auto | User approves the research step; engineering compiles and executes it inside the envelope. |
| auto | manual | Orchestrator chooses the research branch; user approves concrete experiment and execution choices. |
| auto | auto | Both layers act inside their independent approved scopes and return control at their handoff. |

An auto mode delegates decisions; it does not relax integrity checks or expand authority.

## Always protected

Require user approval for destructive operations, history rewrites, deletion of material data,
overwriting user changes, new repositories/remotes/accounts/destinations, secrets or authentication
changes, budget/scope/rig expansion, shared-GPU exceptions, ambiguous Flywheel root or parent,
external publication outside the approved root, and every source-to-target deviation governed by
`integrate-reference-code`. Also require user approval of the complete plot communication
specification before every plotting-code creation or modification: exact title and fixed
`RUN_ID_PARAMS`, visible text defining every plotted metric and its directional interpretation,
exact in-figure placement, and every complete project-relative `plots/` export path including its
leaf filename. Identify the experiment-wide pinned `RUN_ID_PATH_LAYOUT`; construct and show the
complete canonical `nested` path or template first without optimizing it. When two or more fixed
params remain after aggregated params are elided, immediately show exactly one separate
`collapsed-v1` alternative that combines all fixed params, never a partial group, in elected order
as comma-joined, canonically percent-encoded `key=value` components. With zero or one fixed param,
show one path and no separate alternative; label it as the byte-identical rendering of `nested` and
`collapsed-v1` plus the experiment's pinned literal. That path is approvable under either pin
without treating it as a layout change. With two or more, only the form matching the pinned literal
is approvable. Before any output artifact or wave exists, `experiment-design` owns layout election
through the canonical `nested`-first comparison. Once any artifact or wave exists, the pin is
immutable; wanting the other form requires a new numbered sub-experiment, never per-plot approval or
an in-place layout change. Protect `plots/`, the complete experiment path, script stem, and leaf.
Reject collisions and overwrites. If runtime values prevent a concrete path before the edit,
identify every placeholder in the complete path template. Before rendering, show every fully
resolved concrete export path matching the pinned layout, including its leaf filename, and wait for
explicit user approval. An approved template does not approve any concrete destination. A new or
changed resolved path always reopens approval, even when it conforms to the approved template; do
not render before that approval. The agent may only propose this specification. Auto modes cannot
approve it, and a previous approval does not carry across a later plotting-code edit even when the
proposed specification would remain unchanged. An unchanged-code rerender may reuse approval only
when every resolved concrete export path is byte-for-byte identical to the previously explicitly
approved concrete path.

System or sandbox approvals remain independent and always apply.

## Engineering handoff

Give `experiment-design` a self-contained packet containing:

- `research_step_id` and agreement version;
- question, claim, hypothesis, and competing explanations;
- minimal intervention and controlled variables;
- decisive metric/evidence, comparison, acceptance criterion, kill criterion;
- evaluation boundary and prohibited inference;
- allowed compute/time/money, rigs/GPUs, repository/branch, and destinations;
- required controls/baselines and known validity risks;
- requested result granularity and scientific stop condition.

The scientific layer defines what evidence answers the question. The engineering layer chooses how
to encode it as config, run identity, status, checkpoints, smoke tests, artifacts, WandB, and waves.

## Engineering return packet

Require:

- research step, experiment, run, and wave identifiers;
- source tag/revision and environment fingerprint;
- exact commands/config snapshots and placement;
- status and expected final-artifact paths;
- for newly authored plots, persist the experiment-wide pinned `RUN_ID_PATH_LAYOUT`, approved
  complete path templates, and every explicitly approved matching concrete export path in the
  packet;
- measured metrics, uncertainty, failures, exclusions, and deviations;
- resource/time spend and recovery actions;
- links to `EXPERIMENTS.md`, `JOURNAL.md`, logs, evaluations, plots, and checkpoints.

The scientific layer interprets this packet but never rewrites its factual execution state.

## Journal and publication policy

The layer owning an event owns its narrative authorization. A manual layer proposes its journal
entry and waits; an auto layer may append a concise factual entry about its own authorized action.
No mode may rewrite prior journal entries.

In scientific-manual mode, preview a Flywheel publication and wait. In scientific-auto mode, a
logger may publish to the approved canonical root without a per-node prompt. Root ambiguity,
missing decisive evidence, or missing claim/criterion always blocks publication.
