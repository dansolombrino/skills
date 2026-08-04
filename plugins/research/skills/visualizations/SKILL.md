---
name: visualizations
description: Create or update Research 2.0 plots and figures from provenance-valid experiment evaluation outputs while preserving the standard artifact taxonomy and active engineering mode. Use when the user asks to plot, visualize, chart, make figures from results, or add or modify scripts under visualizations/; do not use to recompute missing evaluation data.
---

# visualizations

Plotting code conventions. Canon: `../research-project-init/references/conventions.md`.

Require the Research 2.0 scaffold and read the active execution agreement. Plot representation,
placement, and execution follow `engineering_mode`: manual mode proposes and waits; auto mode may
choose inside the approved envelope. Scientific interpretation and claim changes remain owned by
the scientific layer.

## Placement

- Viz code lives in the **`visualizations/` root folder** (sibling of `code/`), mirroring the experiment hierarchy: `visualizations/NNN_exp/plot_*.py`. `code/` stays purely experiment code.
- Input: **`evaluations/` only** — viz never reads checkpoints or recomputes; if a quantity isn't in evaluations, the producer experiment must export it first.
- Output: under `plots/NNN_exp/<script_stem>/` — one subfolder per script (stem = filename without `.py`).

## Interface: argparse, NOT hydra

Plotting is too dynamic to standardize into configs. Each script takes CLI args via argparse, with argument names matching **1:1** the producer experiment's param names (run_id/config params), plus extra viz-specific args as needed:

```bash
.venv/bin/python visualizations/000_grokking/plot_loss.py --model mlp --lr 1e-3 --seed 0
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

- **No fixed plot checklist**: manual mode asks what the user wants; auto mode chooses the smallest
  visualization that answers the approved scientific handoff.
- Run plainly on **rig-4090** (where evaluations converge) — no dispatch/tmux machinery.
- New plots produced ⇒ apply `$research-journal` according to the event-owning layer's mode when
  they reveal something.
