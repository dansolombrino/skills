---
name: visualizations
description: Create or update plots/figures from experiment evaluation outputs in a structured research project. Use when the user asks to plot, visualize, chart, or make figures from results, or to add/modify scripts under visualizations/.
---

# visualizations

Plotting code conventions. Canon: `../research-project-init/references/conventions.md`.

## Placement

- Viz code lives in the **`visualizations/` root folder** (sibling of `code/`), mirroring the experiment hierarchy: `visualizations/NNN_exp/plot_*.py`. `code/` stays purely experiment code.
- Input: **`evaluations/` only** — viz never reads checkpoints or recomputes; if a quantity isn't in evaluations, the producer experiment must export it first.
- Output: under `plots/NNN_exp/<script_stem>/` — one subfolder per script (stem = filename without `.py`).

## Interface: argparse, NOT hydra

Plotting is too dynamic to standardize into configs. Each script takes CLI args via argparse, with argument names matching **1:1** the producer experiment's param names (run_id/config params), plus extra viz-specific args as needed:

```bash
.venv/bin/python visualizations/000_grokking/plot_loss.py --model mlp --lr 1e-3 --seed 0
```

## Output paths — per-script subfolder + partial run_id rule (default; user has final choice per case)

Each script writes only under `plots/NNN_exp/<script_stem>/` (e.g. `plot_loss.py` → `plots/000_grokking/plot_loss/`). Inside that subfolder, the partial run_id rule applies: a plot sits at the path of the run_id params it holds **fixed**; params it aggregates over are elided:

```
plots/000_grokking/plot_loss/model=mlp/lr=1e-3/seed=0/loss_curve.pdf   # per-run: full run_id path
plots/000_grokking/plot_acc_vs_lr/model=mlp/acc_vs_lr.pdf              # fixed model, aggregated over lr+seed
plots/000_grokking/plot_acc_vs_lr/acc_vs_lr_by_model.pdf               # aggregated over everything
```

Propose the placement for each new plot; the user decides.

## Design & execution

- **No checklist**: visualizations are designed conversationally, per case — ask what the user wants to see, iterate.
- Run plainly on **rig-4090** (where evaluations converge) — no dispatch/tmux machinery.
- New plots produced ⇒ suggest a JOURNAL.md entry if they revealed something (`research-journal` skill).
