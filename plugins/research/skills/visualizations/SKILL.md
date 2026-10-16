---
name: visualizations
description: Create or update Research 2.0 plots as self-contained interactive HTML from provenance-valid experiment evaluation outputs, keep run_id selection inside the file instead of the export path, preserve each single producer's complete numbered hierarchy, run plotting directly on rig-4090 without orchestration or wave machinery, and decide exact titles, title line arrangement, metric explanations, interaction affordances, and fully resolved export paths on every plotting-code edit, stating the reasoning in the reply and the journal. Use when the user asks to plot, visualize, chart, make figures from results, add or modify scripts under visualizations/, or open, view, or serve rendered plots through the read-only plot server; do not use to recompute missing evaluation data.
---

# visualizations

Plotting code conventions. Canon: `../research-project-init/references/conventions.md`.

Directives are closed (see the canon section of that name). Take plot placement, naming, and
style from these directives and this project's own plotting code alone; never copy a plotting
layout or convention from another project on disk.

Require the Research 2.0 scaffold and read the active execution agreement. **Which plots to make**
is mode-owned: manual mode asks what the user wants; auto mode chooses inside the approved envelope.
**Plot communication** — titles, their line arrangement, metric explanations and placement,
interaction affordances, library, and export paths — is agent-decided in both engineering modes
(conventions, "Approval tiers": do, then report), with every check below kept and the reasoning
stated in the reply and the journal. Scientific interpretation and claim changes remain owned by
the user, who may override any part of a plot's specification at any time.

## Placement

- Identify the plot's single producer and its complete numbered leaf hierarchy, including every
  sub-experiment level. Call that hierarchy `<experiment_path>`; for example,
  `000_qat_ptq_metric_equivalence/002_weight_error`.
- Viz code lives at `visualizations/<experiment_path>/<script_stem>.py`, mirroring the complete
  producer hierarchy. `code/` stays purely experiment code.
- Input: **`evaluations/` only**. Read the producer's recorded experiment-wide
  `RUN_ID_PATH_LAYOUT` and resolve inputs with
  `run_id_path(..., layout=RUN_ID_PATH_LAYOUT)` from the canonical helper. Under `hashed-v2` the
  identity params come from `load_run_id_spec(source_path("config/<experiment_path>"))` (the
  tracked `config/<experiment_path>/RUN_ID_VERSIONS.json`) and every run is the
  directory `evaluations/<experiment_path>/rid-<32 hex>/`; read its params from its `.run_id.json`
  or `RUN_ID_MAP.json` and its readable label from `run_id_label`. Under a legacy pin pass
  `segments=RUN_ID_SEGMENTS` when the pin is `segments-v2`/`segments-v1`. A hash is resolved to its
  params through `.run_id.json`, `RUN_ID_MAP.json`, or `run-identity`, never recomputed by hand.
  Never infer layout from slash depth or force `nested`. Viz never reads checkpoints or recomputes; if a quantity is absent
  from evaluations, the producer experiment must export it first.
- Output: under `plots/<experiment_path>/<script_stem>/` — one subfolder per script (stem =
  filename without `.py`) after the complete producer hierarchy, holding one HTML file per figure.
  `RUN_ID_PATH_LAYOUT` governs reads only; it never shapes a plot output path.
- If a visualization consumes multiple producer experiment paths, stop without inferring its
  placement; that taxonomy requires a separate user decision.
- Unit tests for a plotting script are optional and create-on-demand. When one is written, it
  belongs at `tests/visualizations/<experiment_path>/<script_stem>/test_*.py` (pytest).

## Interface: argparse, NOT hydra

Plotting is too dynamic to standardize into configs. Each script takes CLI args via argparse, with argument names matching **1:1** the producer experiment's param names (run_id/config params), plus extra viz-specific args as needed.

These args **scope** which runs the figure covers; they no longer pin the one run it shows.
Default to every run available in `evaluations/` and treat each arg as an optional filter, so the
emitted file carries the whole selectable space:

```bash
# every run:
<environment.name>/bin/python visualizations/000_grokking/002_loss/plot_curve.py
# restricted to one model:
<environment.name>/bin/python visualizations/000_grokking/002_loss/plot_curve.py --model mlp
```

## Output paths — per-script subfolder, one file per figure

Each script writes only below the plot root obtained by replacing its leading `visualizations/`
with `plots/` and removing `.py`. This preserves every numbered hierarchy component and adds the
script stem exactly once. Inside that root a figure is one leaf file, and
run_id selection lives inside the file rather than in the path:

```
plots/000_grokking/plot_loss/loss_curve.html
plots/000_grokking/plot_acc_vs_lr/acc_vs_lr.html
plots/001_compression/002_weight_error/plot_layers/layer_error.html   # nested producer
```

No plot path carries run_id segments: no `key=value` directories, no per-run subfolders anywhere
under `plots/`. Protect `plots/`, the complete `<experiment_path>`, `<script_stem>`, and the leaf
filename — none of them may be collapsed. A script may emit several leaves when it produces
genuinely distinct figures; name each leaf for what it shows, and keep leaf and stem distinct so
variants of one figure stay expressible.

Rewriting a leaf whole is the expected result of every rerun. That self-overwrite needs no new
decision while the resolved path is unchanged. Two different scripts resolving to the same leaf is
still an error: choose a new leaf and report it rather than letting one clobber the other.

Before modifying an existing single-producer plotter, verify that its code and output roots
preserve the producer's complete `<experiment_path>`. Report a flattened or otherwise mismatched
layout and stop; do not move or rewrite artifacts.

## Artifact contract — self-contained interactive HTML

Every plot is one self-contained interactive HTML file at
`plots/<experiment_path>/<script_stem>/<leaf>.html` that a colleague can open in a browser with no
network, no server, and no sibling files:

- **One self-contained file.** Inline every script, style, and datum. No `http(s)://` references,
  no CDN tags, no external asset paths. Confirm it renders correctly from `file://` with the
  network unavailable.
- **Plotly is the default library**, because it satisfies that contract and gives legend series
  toggling natively. A different library for a specific plot is part of its communication
  specification: use one only where the plot needs it and state why in the reply and the journal;
  never mix libraries silently within a project.
- **White background, always.** The page and the figure render on white regardless of the
  reader's browser or OS theme. Set the HTML `body` background to `#ffffff` explicitly, set the
  figure's `paper_bgcolor` and `plot_bgcolor` to `#ffffff` (Plotly's default `plotly` template
  paints the plot area grey, so overriding it is mandatory), and never emit a dark-mode or
  `prefers-color-scheme` variant. Keep text and gridline colors dark enough to stay legible on
  white.
- **File size is not a constraint.** `plots/` is gitignored, so plots are shared by sending the
  file. Never propose committing plot output, and never downsample data to shrink a file.
- **Selection first, figure second.** The page lets the user choose among the run_id params the
  file covers, then renders the figure for that selection. Under `hashed-v2` the selection is keyed
  by run hash and always shows each run's label beside it, so a reader never has to decode a hash.
- **Two download buttons, always.** Every plot carries an "All runs" button that saves the whole
  file and a "Selection" button that saves a trimmed self-contained file holding only the runs the
  current view shows; both save under the leaf filename. Inline this skill's
  `assets/plot-download.js` verbatim as the first `<script>` in `<head>` — the plotting script
  carries its text as a constant, never a path into the skill install — and meet the page
  contract in its header: `data-plot-leaf` on `<html>`, every per-run datum under the `runs`
  object of the `plot-data` JSON block keyed by run_id (the `rid-<32 hex>` hash under `hashed-v2`)
  with `<` escaped, a `window.plotSelectedRuns()` returning the run_ids in view, and rendering only
  from `DOMContentLoaded` or later. The buttons are fixed page furniture, not part of the
  communication specification, and the user cannot decline them. An existing plotter that lacks
  them gains them with its next edit.
- **Offer the affordances the figure's shape earns**: a series toggle whenever it carries two or
  more line series, and a slider or stepper whenever it spans an ordered dimension such as layer,
  epoch, step, or checkpoint. Decide each one as part of the specification and say why; the user
  may decline one. Never impose a fixed interaction checklist beyond what the shape implies.

## Design & execution

- Before every creation or modification of plotting code, read the producer's authoritative
  identity (`load_run_id_spec` under `hashed-v2`; the ordered `RUN_ID_PARAMS` and, when pinned,
  `RUN_ID_SEGMENTS` under a legacy pin), `RUN_ID_PATH_LAYOUT`, metric definitions, evaluation
  schema, and scientific contract. Determine which identity params the figure covers and which of
  them the page lets the reader select.
- Decide one exact communication specification per plot: the title, the visible in-figure metric
  explanation, the interaction affordances, and the complete project-relative export path rooted
  at `plots/` and ending in its leaf filename. Explain what every plotted metric measures and its
  higher/lower/target/range/no-universal-direction interpretation, and decide that text's exact
  placement.
- The title states **every** selected identity param as `key={value}`, so a figure is never
  ambiguous about which runs it shows. Titles are dynamic: they re-render with the selection.
  Decide the title template with every placeholder identified, and give one fully resolved
  example in the report.
- **Decide how the params are arranged across title lines** — which params share a line, in what
  order, and where the semantic part sits — for this plot's reader. This differs per plot: decide
  it each time, never carry one over from another plot, and say why in the report.
- Keep provenance in the file, since `plots/` is gitignored and the file travels alone: the
  selected run_ids (with their labels under `hashed-v2`) in-figure, and the producer
  `<experiment_path>`, the project revision at render time, the render timestamp, and the
  evaluation source paths read in an unobtrusive page-level block. That metadata stays out of the
  title and must never crowd it.
- Dropping run_id segments normally makes the export path fully determined before the code edit.
  If runtime values still prevent that, decide the complete path template and identify every
  placeholder explicitly. Before rendering, resolve every concrete export path, including its leaf
  filename, and reject any destination that collides with a different script's output. A template
  never stands in for a concrete destination: a new or changed resolved path is resolved and
  collision-checked again, even when it conforms to the template.
- Cover multiple axes, panels, derived metrics, and visual encodings separately unless one shared
  explanation is unambiguous. Stop rather than guess when metric semantics are not grounded. Put
  the explanation in an axis label, subtitle, legend, annotation, or in-figure caption; surrounding
  prose alone does not satisfy the requirement.
- Do, then report: edit the plotting code and render without waiting, then state in the reply the
  specification, every fully resolved concrete export path, and the reasoning behind each choice,
  and record the same in the `research-journal` entry for the plot. Every later plotting-code edit
  decides, checks, and reports its specification again, even when it is unchanged.
- After rendering, open the file and verify that the decided text is present, legible, and
  unchanged in the page's default view, that every decided affordance actually works, and that
  the decided strings are present in the HTML source, that the page and plot area
  are white, and that both download buttons save a file that reopens offline — the selection
  file holding only the runs in view. When a check fails, fix the code, render again, and report
  the correction; never hide a failed check.
- **Hand the user each rendered file as a URL.** Run this skill's
  `scripts/plot_server.py url <file>` on the rendering machine for every leaf written. When the
  host can open a browser tab inside the user's IDE for the project's workspace, open the printed
  `local:` URL there; otherwise print the `local:` and `public:` URLs. Never start an ad-hoc server,
  copy the file elsewhere, or print the access token. If the file is not servable, the server is
  not answering, or it does not serve the file yet (a sandboxed server publishes a new project
  within 2 minutes), say so and point to `references/plot-server.md`; the file path stays the
  fallback.
- **Run lookup.** The plot server's **Runs** view (header toggle) shows hash ↔ params for one
  experiment from its `evaluations/<experiment_path>/RUN_ID_MAP.json` and the tracked
  `config/<experiment_path>/RUN_ID_{VERSIONS,HISTORY}.json` (or the copies the map embeds): filter
  by `key=value`, paste a hash or prefix (an old hash resolves through `RUN_ID_HISTORY.json`), copy
  a run's config, and jump to the plots that include it. The map embeds every run's resolved
  config, so the Runs view exposes those configs to anyone who can open the page (never `.env`). Point the user there when they ask which run a hash in a plot is.
- **No fixed plot checklist**: manual mode asks what the user wants; auto mode chooses the smallest
  visualization that answers the question the plot is for.
- Treat every plotting render as a direct, non-orchestrated fast path. Run its argparse command in
  the foreground on **`rig-4090`**, where evaluations converge. When another host is active,
  connect directly to `rig-4090` and run from its project checkout; stop if that checkout cannot
  be identified or reached.
- Do not invoke `sweep-dispatch`, `rig-sync`, or
  `experiments-tracking` for plotting. Do not create orchestration control state, mint a wave id,
  generate launch scripts, dispatch work, start tmux, or add/update `EXPERIMENTS.md` rows.
- Every plotting-code edit and render ⇒ append a `research-journal` entry with the specification,
  the resolved paths, and the reasoning (do, then report), plus what the plot reveals when it
  reveals something.
