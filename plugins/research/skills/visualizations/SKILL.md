---
name: visualizations
description: Create or update Research 2.0 plots and figures from provenance-valid experiment evaluation outputs, run plotting directly on rig-4090 without orchestration or wave machinery, preserve the standard artifact taxonomy, and require user approval of exact RUN_ID-aware title templates before every plotting-code edit. Use when the user asks to plot, visualize, chart, make figures from results, or add or modify scripts under visualizations/; do not use to recompute missing evaluation data.
---

# visualizations

Plotting code conventions. Canon: `../research-project-init/references/conventions.md`.

Require the Research 2.0 scaffold and read the active execution agreement. Plot representation,
placement, and execution follow `engineering_mode`: manual mode proposes and waits; auto mode may
choose inside the approved envelope. Scientific interpretation and claim changes remain owned by
the scientific layer. Plot-title approval is always user-owned and overrides both modes.

## Placement

- Viz code lives in the **`visualizations/` root folder** (sibling of `code/`), mirroring the experiment hierarchy: `visualizations/NNN_exp/plot_*.py`. `code/` stays purely experiment code.
- Input: **`evaluations/` only** — viz never reads checkpoints or recomputes; if a quantity isn't in evaluations, the producer experiment must export it first.
- Output: under `plots/NNN_exp/<script_stem>/` — one subfolder per script (stem = filename without `.py`).

## Interface: argparse, NOT hydra

Plotting is too dynamic to standardize into configs. Each script takes CLI args via argparse, with argument names matching **1:1** the producer experiment's param names (run_id/config params), plus extra viz-specific args as needed:

```bash
<environment.name>/bin/python visualizations/000_grokking/plot_loss.py --model mlp --lr 1e-3 --seed 0
```

## Output paths — per-script subfolder + partial run_id rule

Each script writes only under `plots/NNN_exp/<script_stem>/` (e.g. `plot_loss.py` → `plots/000_grokking/plot_loss/`). Inside that subfolder, the partial run_id rule applies: a plot sits at the path of the run_id params it holds **fixed**; params it aggregates over are elided:

```
plots/000_grokking/plot_loss/model=mlp/lr=1e-3/seed=0/loss_curve.pdf   # per-run: full run_id path
plots/000_grokking/plot_acc_vs_lr/model=mlp/acc_vs_lr.pdf              # fixed model, aggregated over lr+seed
plots/000_grokking/plot_acc_vs_lr/acc_vs_lr_by_model.pdf               # aggregated over everything
```

Resolve placement with the applicable engineering-mode owner and record nonstandard choices.

## Design & execution

- Before every creation or modification of plotting code, read the producer's authoritative
  ordered `RUN_ID_PARAMS` and classify each parameter as fixed or aggregated for every plot the
  script produces.
- Propose the exact title template for every plot, including wording, punctuation, formatting,
  and each fixed RUN_ID param as `key={value}` in elected order. Omit aggregated RUN_ID params;
  describe the aggregation semantically only when it helps interpret the plot. If none are fixed,
  state that the proposed semantic title contains no RUN_ID part rather than fabricating one.
- Wait for explicit user approval before editing the plotting code. Re-propose and obtain approval
  on every later plotting-code edit even when the title template is unchanged. Neither scientific-
  auto nor engineering-auto may bypass this gate; approval of a title does not authorize unrelated
  protected actions.
- **No fixed plot checklist**: manual mode asks what the user wants; auto mode chooses the smallest
  visualization that answers the approved scientific handoff.
- Treat every plotting render as a direct, non-orchestrated fast path. Run its argparse command in
  the foreground on **`rig-4090`**, where evaluations converge. When another host is active,
  connect directly to `rig-4090` and run from its project checkout; stop if that checkout cannot
  be identified or reached.
- Do not invoke `$scientific-orchestrator`, `$sweep-dispatch`, `$rig-sync`, or
  `$experiments-tracking` for plotting. Do not create orchestration control state, mint a wave id,
  generate launch scripts, dispatch work, start tmux, or add/update `EXPERIMENTS.md` rows.
- New plots produced ⇒ apply `$research-journal` according to the event-owning layer's mode when
  they reveal something.
