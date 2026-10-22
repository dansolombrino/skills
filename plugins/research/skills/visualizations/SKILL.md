---
name: visualizations
description: Create or update Research 2.0 plots as self-contained interactive HTML from experiment evaluation outputs - titles that state the selected params by full name, meaningful axis and metric text, a PARAM_GLOSSARY.json with hover explanations, run selection inside the file, one file per figure under the producer's complete numbered hierarchy, and a plot-server URL for each render. Use when the user asks to plot, visualize, chart, make figures from results, add or modify scripts under visualizations/, or open, view, or serve rendered plots through the read-only plot server; do not use to recompute missing evaluation data.
---

# visualizations

Plots read `evaluations/` and write self-contained HTML under `plots/`. Project facts:
`../research-project-init/references/conventions.md`. The read-only plot server:
[references/plot-server.md](references/plot-server.md).

## Placement

- The producer's complete numbered leaf hierarchy is `<experiment_path>`, e.g.
  `000_qat_ptq_metric_equivalence/002_weight_error`. Code lives at
  `visualizations/<experiment_path>/<script_stem>.py` and writes only below
  `plots/<experiment_path>/<script_stem>/`, one HTML file per figure:

  ```
  plots/000_grokking/plot_loss/loss_curve.html
  plots/001_compression/002_weight_error/plot_layers/layer_error.html
  ```

- Plot paths carry no run_id: no `key=value` directories, no per-run folders. Name each leaf for
  what it shows. Rerunning rewrites a leaf whole; two different scripts resolving to the same leaf
  is an error — resolve every concrete export path before rendering and check it against other
  scripts' outputs.
- A plot that combines multiple producer experiment paths has no defined home: ask the user. An
  existing plotter whose paths flatten the hierarchy is reported, not moved.
- Inputs are `evaluations/` only, through `run_id_path(..., layout=RUN_ID_PATH_LAYOUT)` with
  params from `load_run_id_spec(source_path("config/<experiment_path>"))`; each run is
  `evaluations/<experiment_path>/rid-<32 hex>/`, its params in `.run_id.json` or `RUN_ID_MAP.json`.
  Training curves are its `metrics.jsonl`, read with `read_metrics` (`code/common/metrics.py`).
  A quantity missing from evaluations is exported by the producer first; plots never read
  checkpoints. An experiment pinned to an older layout: see `run-identity`
  references/legacy-layouts.md.
- Tests, when wanted: `tests/visualizations/<experiment_path>/<script_stem>/test_*.py`.

## Interface

argparse, not hydra. Argument names match the producer's param names 1:1 and act as optional
filters; by default the file covers every run in `evaluations/`, so the reader can select among
them:

```bash
<environment.name>/bin/python visualizations/000_grokking/002_loss/plot_curve.py --model mlp
```

## Communicating clearly

Decide these for every plot, each time the plotting code changes:

- **Title.** It states every selected identity param as `Full name = {value}` with the glossary's
  full name, never the raw key, and re-renders with the selection. Arrange params across title
  lines for this plot's reader rather than reusing another plot's arrangement. With no fixed
  param, use a plain semantic title.
- **Axes and metrics.** Name every axis and say, inside the figure (axis label, subtitle, legend,
  annotation, or caption), what each plotted metric measures and whether higher, lower, a target,
  or no direction is better. Cover separate panels, derived metrics, and encodings separately
  unless one explanation fits all. When the producer's code and schema don't ground a metric's
  meaning, ask rather than guess.
- **Selector.** Each selectable param shows its glossary full name only, followed by an **(i)**
  icon whose hover shows the explanation. Under `hashed-v2` the selection is keyed by run hash and
  shows each run's params beside it in the same full names (`Full name = value, …`).
- **Affordances.** A series toggle for two or more line series, a slider or stepper for an
  ordered dimension (layer, epoch, step, checkpoint); nothing the figure's shape doesn't call for.
- **Provenance.** The selected run_ids in the figure; the producer `<experiment_path>`, project
  revision, render time, and evaluation paths in an unobtrusive page block, never in the title.
- Choose the smallest visualization that answers the question.

## Param glossary

`config/<experiment_path>/PARAM_GLOSSARY.json`, tracked beside `RUN_ID_VERSIONS.json`; not
identity, so editing it never changes a run_id:

```json
{"lr": {"name": "Learning rate", "explanation": "Step size of each optimizer update; higher learns faster but can diverge."}}
```

The name is the plain-language full name; the explanation says what the param controls and what
changing it does, in a sentence or two grounded in the producer's config and code. Read entries
with `load_param_glossary(source_path("config/<experiment_path>"), params)`, which fails on a
missing entry: write the entry rather than fall back to the raw key. Entries are reused by later
plots, and the user may reword any of them. Commit the glossary with the plotting code.

## The HTML file

- One self-contained file: every script, style, and datum inline, no network references; it
  must render from `file://` offline. Plotly is the default library. `plots/` is gitignored, so
  plots travel as files; size is not a constraint and data is never downsampled.
- White page and plot area always (`body` `#ffffff`, `paper_bgcolor` and `plot_bgcolor`
  `#ffffff`), no dark-mode variant.
- Inline this skill's `assets/plot-download.js` verbatim as the first `<script>` in `<head>` (it
  adds "All runs" and "Selection" download buttons) and `assets/plot-shell.js` verbatim as the
  second, before Plotly (the reader toolbar: hide the selection, zoom, export, fullscreen).
  Carry their text as constants in the plotting script, never as paths into the skill install.
  The page contract they need: `data-plot-leaf` on `<html>`; per-run data under `runs` in the
  `plot-data` JSON block keyed by run_id, with `<` escaped; `window.plotSelectedRuns()` returning
  the run_ids in view; rendering from `DOMContentLoaded` or later; the selection container marked
  `data-plot-controls` and the provenance block `data-plot-provenance`; drawing only through
  `Plotly.newPlot` or `Plotly.react`. Both scripts are fixed furniture on every plot; don't build
  a second zoom, hide, or export control.

## Render and hand over

- Plotting is a direct command, not a launch: run the argparse command in the foreground on
  `rig-4090`, where evaluations converge, from its project checkout. No wave, tmux, `sweep-dispatch`,
  `rig-sync`, or EXPERIMENTS.md rows.
- After rendering, open the file and check that the title, axis, and metric text are present and
  legible, the (i) hovers work, the page is white, the affordances work, and both download buttons
  produce files that reopen offline.
- Give the user each rendered file as a link from `scripts/plot_server.py url <file>` (this
  skill's installed directory) run on the rendering machine: the `public:` URL first, the
  `local:` one only as a secondary line. If no `public:` line prints or the server does not serve
  the file yet, say so and point to `references/plot-server.md`.
- The server's **Runs** view maps hashes to params for an experiment; point the user there for
  "which run is this hash".
- A plot that reveals something gets a `research-journal` entry.
- After a wave finishes, `sweep-supervisor`'s `analyze` follow-up applies this skill unattended,
  with the same rules.
