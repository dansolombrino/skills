---
name: flywheel-auto
description: Advance a Flywheel frontier autonomously with Flywheel MCP or CLI under an explicit budget by persisting control state, executing branches, and replanning after each resolution. Use for autonomous research continuation, not planning-only lookahead, claim validation, or source-to-graph authoring.
---

# flywheel-auto

## When To Use

Use this skill when the task is to shape information inside Flywheel rather than only discuss it. Use the flywheel-auto skill when the user wants Flywheel to keep advancing a research frontier autonomously under a specified budget. Flywheel MCP is a node-first durable system of record for research work: nodes hold durable state, artifacts hold supporting files, executions record runs, and compute leases provide managed hardware. Conceptually, the flywheel-auto skill is what you get when you invoke `flywheel-lookahead`, then execute and replan after each resolution: persist the run contract in the graph, keep the plan `n` hops ahead, and spend only within an explicit measured budget.

If exact tool or field semantics are unclear, load the shared [Flywheel MCP tool map](../flywheel/references/flywheel-mcp-tool-map.md) and [interfaces contract](../flywheel/references/INTERFACES.md) before mutating nodes or acquiring compute.

Before any mutation or compute action, run the mandatory
[Research Root Gate](../flywheel/references/research-root-gate.md). Every explicit,
focused, recent, start, parent, and control node must be proven by ancestry to belong
to the resolved workspace root; contextual recency never bypasses this gate.

In a Research 2.0 project, read the execution agreement first. Scientific-auto is required for
autonomous frontier selection; engineering mode owns compute acquisition. Engineering-manual waits
for the offer choice. Engineering-auto may select and acquire from an already approved grant only
inside the recorded provider/SKU/credit envelope. Any product-level approval prompt, new grant,
budget expansion, unknown lease cleanup, or destructive graph action remains protected.

## Modes

This skill supports both Flywheel install modes:

- **`--mode mcp` install**: route via the shared [Flywheel MCP tool map](../flywheel/references/flywheel-mcp-tool-map.md); tool calls take the form `flywheel_<tool>` (e.g., `flywheel_commit_node`).
- **`--mode cli` install**: `setup --mode cli` configures runtime credentials, then route via the shared [Flywheel CLI tool map](../flywheel/references/flywheel-cli-tool-map.md); tool calls take the form `flywheel <command>` (e.g., `flywheel nodes:commit`). Prerequisite for bare-binary routing: run the curl installer with `--mode cli` or use the managed-prefix npm install recipe from the README.

When switching an already-configured host between modes, rerun setup with `--force` (or uninstall first) so prior-mode artifacts are reconciled explicitly.

The Workflow below cites both the MCP tool name and its CLI equivalent inline at each imperative step. Pick the one matching your install mode.

## Input Contract

Before execution, recover or establish these inputs:

- Required before execution: start nodes, objective, budget ceiling, budget unit, and an explicit terminal condition.
- Optional with defaults: `lookahead_depth` defaults to `n=1`; `frontier_width` defaults to `k=1` and also caps concurrent executable branches.
- Recovery rules:
  - Ask only when a required execution input cannot be recovered from explicit user instructions, current conversational context, or recent graph state.
  - If start nodes cannot be recovered from explicit ids/slugs, named current context, or recent graph context, ask once.
  - If the objective is missing, ask once; if the user refuses, infer it from graph context and persist it before execution.
  - If budget ceiling or budget unit is missing, ask once before execution.
  - If budget ceiling and budget unit are present but terminal condition is missing, set `terminal_condition: "budget ceiling reached"` and persist that derivation in the control node `content` before compute request or acquisition.
  - If the user gives a non-credit budget unit, persist that exact user-facing unit and derive the operational compute approval cap before acquisition.

## Working Terms

- `frontier`: the current unresolved next-step nodes or candidate branches worth planning or executing next.
- `resolved frontier`: the latest resolved nodes from which the next frontier should be expanded, not the original source corpus again.
- `graph-local`: continuation and stopping decisions should be recoverable from persisted node state alone, not from chat memory.
- `control node`: the dedicated `insight` node that stores the canonical run contract for the current frontier.

## Core Rules

- Treat the main page body as the node readme/body field (`content` on modern Flywheel surfaces).
- Use artifacts for supporting files and evidence, not as a substitute for the node's main narrative.
- Only create graph edges for durable semantic relationships, because graphifying every wiki link floods the graph with noise that hides decision-relevant structure.
- Treat the flywheel-auto skill as graph-local autonomous research: persist the run contract in the graph, keep the plan `n` hops ahead, and spend only within an explicit measured budget.
- When guidance from flywheel, flywheel-lookahead, flywheel-reproduce, or flywheel-to-graph conflicts during an active flywheel-auto run, flywheel-auto rules take precedence for compute, questioning, and stop decisions.
- Ask only the minimum clarification questions needed to establish a coherent control contract, and ask none when the required answers are already recoverable from user instructions, conversational context, or graph state.
- Planned nodes that are expected to produce evidence or artifacts should say so in `content` and attach artifacts when evidence exists; planned nodes that encode synthesis, decomposition, or decision structure should keep that rationale in `content`.
- Do not rely on fresh user feedback to decide whether the flywheel-auto skill should continue, so later replans can resume from node state alone in a fresh chat.
- If a branch will author a plot, load the protected plot communication rules in [the autonomous
  experiment gate](references/experiment-design-protocol-autonomous.md). Propose the exact title,
  visible metric meaning/direction text, in-figure placement, and every complete project-relative
  `plots/` export path including its leaf filename. Identify the experiment-wide pinned
  `RUN_ID_PATH_LAYOUT` and show the canonical `nested` path or template first without optimizing it.
  When at least two fixed params remain, immediately show exactly one separate `collapsed-v1`
  alternative combining all fixed params in elected order as comma-joined, canonically
  percent-encoded `key=value` components. With zero or one fixed param, show one path and no
  separate alternative; label it as the byte-identical rendering of `nested` and `collapsed-v1` plus
  the experiment's pinned literal. That path is approvable under either pin without treating it as a
  layout change. With two or more, only the form matching the pinned literal is approvable. Before
  any output artifact or wave exists, `experiment-design` owns layout election through the canonical
  `nested`-first comparison. Once any artifact or wave exists, the pin is immutable; wanting the
  other form requires a new numbered sub-experiment, never per-plot approval or an in-place layout
  change. Never collapse a partial group or the root, experiment path, script stem, or leaf; elide
  aggregated params in both layouts. Wait for explicit user acceptance before creating or modifying
  plot-producing code. Reject collisions and overwrites. Ground metric meaning, direction, units,
  and relevant ranges in the control contract or evaluation schema; stop rather than infer missing
  semantics. Persist the pinned layout, accepted template, and explicitly approved matching concrete
  paths in control-node `content`. An approved template does not approve any concrete destination.
  This gate overrides autonomy; unchanged-code rerenders may reuse it only for identical resolved
  export paths, while extracted source figures and external artifacts are exempt.

## Workflow

1. Resolve the starting context.
   - Resolve starting nodes in this order: explicit node ids/slugs, explicitly named current context, focused or recently referenced nodes, then ask the user if nothing stable is recoverable.
2. Resolve the objective and budget contract.
   - Recover required execution inputs from the current conversation and graph first. Ask only for inputs that remain genuinely missing after that recovery pass.
   - The flywheel-auto skill requires a measurable objective and an explicit terminal condition. Ask once for missing prerequisites; if the user refuses to specify an objective, infer it from the available graph context and state it explicitly before continuing.
   - The budget contract must be explicit before execution starts. Ask once for missing budget ceiling or budget unit. When budget ceiling and budget unit are present but terminal condition is missing, derive `terminal_condition: "budget ceiling reached"` and persist that derivation into the control node `content` before any compute request or acquisition.
   - Managed compute spend is billed in Flywheel credits. If the user gives dollars, hours, or another measurable budget semantic, persist that user-facing cap explicitly and derive the operational compute approval cap before acquisition.
   - Persist the run contract in a dedicated control node for this frontier. Put the canonical contract and brief run rationale in node `content`, and keep a one-line synopsis in `summary`.
   - The control contract must name: objective, decision criterion, start nodes, budget ceiling and unit, lookahead depth `n`, frontier width `k`, and an explicit terminal condition.
   - Later flywheel-auto replans must read the control node `content` first and continue from that persisted contract rather than from chat memory.
   - Run the design gate from [references/experiment-design-protocol-autonomous.md](references/experiment-design-protocol-autonomous.md) before the first compute request: confirm objective, decision criterion, evidence plan, budget readiness, and stop-reason recording requirements, even though later continuation decisions become graph-local.
3. Map each stable page, claim, or concept to a node.
   - For exact node-mutation shapes, load the shared [Flywheel MCP tool map](../flywheel/references/flywheel-mcp-tool-map.md).
   - In Research 2.0, create every new node with `flywheel_branch_node` (MCP) or
     `flywheel nodes:branch` (CLI) from the canonical root or another ancestry-verified
     parent. `commit_new_node` is reserved for non-Research standalone nodes or an
     explicitly approved new root.
   - Update an existing node with `flywheel_get_node` + `flywheel_acquire_stage_lease` + `flywheel_commit_node` (MCP) or `flywheel nodes:get` + `flywheel nodes:stage:lease:acquire` + `flywheel nodes:commit` (CLI) when continuing work.
4. Put the primary page markdown or plan rationale in the node body/readme field (`content`).
   - Put claims, rationale, methods, outcomes, and continuation notes in `content`; keep `summary` concise.
5. Publish supporting files with the artifact upload flow.
   - For exact artifact contract details, load the shared [artifact contract](../flywheel/references/ARTIFACTS.md).
   - Use `flywheel_prepare_artifact_uploads` (MCP) or `flywheel artifacts:upload:prepare` (CLI).
   - Upload raw file bytes to the returned signed URLs.
   - Call `flywheel_finalize_artifact_uploads` (MCP) or `flywheel artifacts:upload:finalize` (CLI) once all uploads in the batch are staged.
   - Keep the artifact filename stable enough to reference from markdown when inline rendering matters.
6. Add graph structure sparingly.
   - Promote only decision-relevant relationships to edges.
   - Leave ordinary cross-links inside the markdown body.
7. Expand the frontier deliberately.
   - Expand from the resolved frontier rather than mirroring the source corpus again.
   - Depth `n` means plan `n` hops ahead from the currently resolved frontier. Default `n=1`.
   - Width `k` means plan up to `k` distinct non-redundant frontier directions. Default `k=1`.
   - In the flywheel-auto skill, the same `k` also caps concurrent workers pursuing executable frontier nodes.
   - Keep unresolved plan nodes staged until the corresponding work is resolved.
8. Execute and replan continuously.
   - For exact execution and compute request shapes, load the shared [Flywheel MCP tool map](../flywheel/references/flywheel-mcp-tool-map.md) and [references/experiment-design-protocol-autonomous.md](references/experiment-design-protocol-autonomous.md).
   - Use `flywheel_launch_execution` (MCP) or `flywheel executions:launch` (CLI) when the branch can run as a Flywheel node execution without a separate leased machine, and inspect terminal status before commit.
   - Use managed compute when the branch needs provider or SKU choice, SSH access, a custom runtime, or longer-lived hardware. In MCP mode use `flywheel_request_compute_grant_approval`, resolve the approved `compute_grant_id` with `flywheel_list_compute_grants(status=active, approval_session_id=<session_id>)`, call `flywheel_compute_list_options`, and recommend one offer deterministically with up to two alternatives. Engineering-manual waits for explicit user confirmation or override. Engineering-auto may choose an in-envelope offer from the already-approved grant and record the selection. Then use `flywheel_compute_acquire`, poll `flywheel_compute_status`, and use `flywheel_compute_connection` when ready. In CLI mode use the parallel `flywheel compute-grants:request-approval` → `flywheel compute-grants:list` → `flywheel compute:options` → `flywheel compute:acquire` → `flywheel compute:status` → `flywheel compute:connection` sequence.
   - Spawn up to `min(k, available subagent slots minus one)` workers for distinct executable frontier nodes. When no worker slot is available, execute branches sequentially; `k` remains the frontier-width ceiling, not a promise of concurrency.
   - For `k > 1`, apply the list-options and recommendation boundary per worker; manual mode
     confirms each offer, while auto mode verifies and records each offer against the shared envelope.
   - Route additional viable branches sequentially when there are more than `k` worthwhile directions.
   - After each resolved node, refresh the lookahead so the graph remains `n` hops ahead.
   - Release managed compute when a branch is done. Default to releasing only
     known leases. Use release-all with the active `lease_control_token` for
     token-scoped bulk cleanup; add `force=true` (MCP) or `--force --yes` (CLI)
     only when the user explicitly requests account-wide cleanup.
   - Stop only when the graph-local terminal condition says to stop, such as: objective met, budget exhausted, or no non-redundant frontier branch is likely to produce enough decision-relevant information to justify the remaining budget and duplication risk.
   - If stopping because no non-redundant frontier branch is worth pursuing, record a per-candidate rejection rationale in control node `content` before termination.
   - Before every termination path, persist `stop_reason` in control node `content` as one of: `budget_exhausted`, `objective_met`, `no_viable_branch`, `user_cancelled`, `runtime_error`.

9. Commit after the node snapshot is coherent with `flywheel_commit_node` (MCP) or `flywheel nodes:commit` (CLI).
   - Commit resolved nodes once `content`, `summary`, artifacts, tags, executions, and graph edges make the snapshot coherent.
   - Leave unresolved plan nodes staged.

## Result Contract

When the skill completes a pass, it should leave behind:

- One persisted control node containing the canonical run contract.
- Zero or more staged frontier nodes up to the current `n` and `k` limits.
- Zero or more committed resolved nodes, once their durable content and artifacts are coherent.
- Uploaded artifacts for completed empirical work when evidence exists, or an explicit artifact-free rationale in node content when artifacts are absent by design.
- Every newly authored plot includes its accepted visible metric explanation, with the accepted
  communication specification persisted in control-node content.
- Released managed compute if any lease was acquired during the pass.
- An explicit `stop_reason` recorded in node state so a later pass can resume graph-locally.
- If the no-branch stop clause is used, a per-candidate rejection log recorded in control node `content`.

## Persisted Control Contract

Use a dedicated node as the durable run controller for the current
frontier. Do not rely on unstated product fields or fresh chat context.

For the canonical entity model and public contract terminology behind this
controller, load the shared [interfaces contract](../flywheel/references/INTERFACES.md).

Use this storage convention:

1. Reuse an existing control node when one already governs the same frontier;
   otherwise, in Research 2.0, branch it from the verified canonical root or governing
   frontier node. Use `commit_new_node` only outside Research 2.0 or with explicit
   approval to create a new root.
2. Put the canonical contract in node `content`.
3. Put a one-line synopsis in `summary`.
4. Put the run rationale, continuation rule, or prioritization logic in `content`.
5. Before committing control-node edits, acquire or refresh the stage lease with `flywheel_acquire_stage_lease` (and `flywheel_heartbeat_stage_lease` for long edits) — or in CLI mode, `flywheel nodes:stage:lease:acquire` and `flywheel nodes:stage:lease:heartbeat`.
6. Read the control node with `flywheel_get_node` (MCP) or `flywheel nodes:get` (CLI) before later replans that need
   a fresh `expected_revision` revision token for optimistic-locking writes.
7. Commit the control node with `flywheel_commit_node` (MCP) or `flywheel nodes:commit` (CLI) once the contract is
   coherent, even if executable frontier nodes remain staged or in progress.

Canonical contract shape:

```md
## Run contract

- Objective:
- Decision criterion:
- Start nodes:
- Budget ceiling:
- Budget unit:
- Compute approval cap:
- Lookahead depth:
- Frontier width:
- Terminal condition:
- Stop reason:
- Approved plot communication specifications: none
```

Use `Compute approval cap` for the operational cap that will govern managed compute acquisition. If
the user already budgets directly in credits, it can match `Budget ceiling`. If the user budgets in
another unit, keep both values so later continuations can recover the user-facing constraint and the
executable approval cap from node state alone. Replace `none` only after explicit user acceptance,
recording each plot's identifier, exact title, visible metric explanation, in-figure placement,
pinned `RUN_ID_PATH_LAYOUT`, and approved export path or path template with all placeholders
identified. Before rendering, show every fully resolved concrete export path, including its leaf
filename, and wait for explicit user approval. An approved template does not approve any concrete
destination. A new or changed resolved path always reopens approval, even when it conforms to the
approved template; do not render before that approval. An unchanged-code rerender may reuse approval
only when every resolved concrete export path is byte-for-byte identical to the previously
explicitly approved concrete path.

## Guardrails

- Do not blur `flywheel-reproduce` and the flywheel-auto skill, because `flywheel-reproduce` validates existing claims under budget while the flywheel-auto skill expands the frontier to create new knowledge.
- Build the graph explicitly with nodes, artifacts, and selected edges.
- Keep the flywheel-auto skill graph-local: future continuation and stopping decisions should be derivable from the persisted node state rather than from fresh chat context.
- Do not ask follow-up questions after the control contract is coherent unless a contradiction, missing required approval, or tool/runtime failure makes autonomous continuation impossible.
- Never treat the coherent control contract, autonomous mode, silence, or the agent's own recommendation
  as approval of a plot communication specification.

See also: invoke `flywheel-to-graph` to port source material into Flywheel without implicit execution, invoke `flywheel-reproduce` to graphify claim-bearing sources and run budgeted validation branches, and invoke `flywheel-lookahead` to stage next-step frontier nodes from existing graph state without execution.
