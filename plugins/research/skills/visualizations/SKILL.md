---
name: visualizations
description: Create or update Research 2.0 plots and figures from provenance-valid experiment evaluation outputs, preserve each single producer's complete numbered hierarchy, run plotting directly on rig-4090 without orchestration or wave machinery, and require user approval of exact titles plus visible metric explanations before every plotting-code edit. Use when the user asks to plot, visualize, chart, make figures from results, or add or modify scripts under visualizations/; do not use to recompute missing evaluation data.
---

# visualizations

Plotting code conventions. Canon: `../research-project-init/references/conventions.md`.

Directives are closed (see the canon section of that name). Take plot placement, naming, and
style from these directives and this project's own plotting code alone; never copy a plotting
layout or convention from another project on disk.

Require the Research 2.0 scaffold and read the active execution agreement. Plot representation,
placement, and execution follow `engineering_mode`: manual mode proposes and waits; auto mode may
choose inside the approved envelope. Scientific interpretation and claim changes remain owned by
the scientific layer. Plot communication approval is always user-owned and overrides both modes.

## Placement

- Identify the plot's single producer and its complete numbered leaf hierarchy, including every
  sub-experiment level. Call that hierarchy `<experiment_path>`; for example,
  `000_qat_ptq_metric_equivalence/002_weight_error`.
- Viz code lives at `visualizations/<experiment_path>/<script_stem>.py`, mirroring the complete
  producer hierarchy. `code/` stays purely experiment code.
- Input: **`evaluations/` only** — viz never reads checkpoints or recomputes; if a quantity isn't in evaluations, the producer experiment must export it first.
- Output: under `plots/<experiment_path>/<script_stem>/<partial_run_id path>/` — one subfolder per
  script (stem = filename without `.py`) after the complete producer hierarchy.
- If a visualization consumes multiple producer experiment paths, stop without inferring its
  placement; that taxonomy requires a separate user decision.
- Unit tests for a plotting script are optional and create-on-demand. When one is written, it
  belongs at `tests/visualizations/<experiment_path>/<script_stem>/test_*.py` (pytest).

## Interface: argparse, NOT hydra

Plotting is too dynamic to standardize into configs. Each script takes CLI args via argparse, with argument names matching **1:1** the producer experiment's param names (run_id/config params), plus extra viz-specific args as needed:

```bash
<environment.name>/bin/python visualizations/000_grokking/002_loss/plot_curve.py --model mlp --lr 1e-3 --seed 0
```

## Output paths — per-script subfolder + partial run_id rule

Each script writes only below the plot root obtained by replacing its leading `visualizations/`
with `plots/` and removing `.py`. This preserves every numbered hierarchy component and adds the
script stem exactly once. Inside that root, the partial run_id rule applies: a plot sits at the
path of the run_id params it holds **fixed**; params it aggregates over are elided:

```
plots/000_grokking/plot_loss/model=mlp/lr=1e-3/seed=0/loss_curve.pdf   # per-run: full run_id path
plots/000_grokking/plot_acc_vs_lr/model=mlp/acc_vs_lr.pdf              # fixed model, aggregated over lr+seed
plots/000_grokking/plot_acc_vs_lr/acc_vs_lr_by_model.pdf               # aggregated over everything
plots/001_compression/002_weight_error/plot_layers/model=vit/seed=0/layer_error.pdf  # nested producer
```

Before modifying an existing single-producer plotter, verify that its code and output roots preserve
the producer's complete `<experiment_path>`. Report a flattened or otherwise mismatched layout and
obtain explicit user authorization before moving code or artifacts; never migrate it silently.

## Design & execution

- Before every creation or modification of plotting code, read the producer's authoritative
  ordered `RUN_ID_PARAMS`, metric definitions, evaluation schema, and scientific contract. Classify
  each RUN_ID parameter as fixed or aggregated for every plot the script produces.
- Propose one exact communication specification per plot. Include the title wording, punctuation,
  formatting, and each fixed RUN_ID param as `key={value}` in elected order. Also propose visible
  in-figure text that explains what every plotted metric measures, its higher/lower/target/range/no-
  universal-direction interpretation, and the text's exact placement. Omit aggregated RUN_ID
  params; describe aggregation semantically only when it helps interpretation. If none are fixed,
  state that the semantic title contains no RUN_ID part rather than fabricating one.
- Cover multiple axes, panels, derived metrics, and visual encodings separately unless one shared
  explanation is unambiguous. Stop rather than guess when metric semantics are not grounded. Put
  the explanation in an axis label, subtitle, legend, annotation, or in-figure caption; surrounding
  prose alone does not satisfy the requirement.
- Propose only. Wait for the user's explicit acceptance before editing plotting code.
  Re-propose and obtain approval on every later plotting-code edit even when the specification is
  unchanged. Neither scientific-auto nor engineering-auto may bypass this gate. Repeated renders
  of unchanged approved code need no new approval.
- After rendering, inspect the figure and verify that the accepted text is present, legible, and
  unchanged. Do not silently repair wording or placement; propose any correction and reopen the
  approval gate before editing.
- **No fixed plot checklist**: manual mode asks what the user wants; auto mode chooses the smallest
  visualization that answers the approved scientific handoff.
- Treat every plotting render as a direct, non-orchestrated fast path. Run its argparse command in
  the foreground on **`rig-4090`**, where evaluations converge. When another host is active,
  connect directly to `rig-4090` and run from its project checkout; stop if that checkout cannot
  be identified or reached.
- Do not invoke `scientific-orchestrator`, `sweep-dispatch`, `rig-sync`, or
  `experiments-tracking` for plotting. Do not create orchestration control state, mint a wave id,
  generate launch scripts, dispatch work, start tmux, or add/update `EXPERIMENTS.md` rows.
- New plots produced ⇒ apply `research-journal` according to the event-owning layer's mode when
  they reveal something.
