---
name: visualizations
description: Create or update Research 2.0 plots as self-contained interactive HTML from provenance-valid experiment evaluation outputs, keep run_id selection inside the file instead of the export path, preserve each single producer's complete numbered hierarchy, run plotting directly on rig-4090 without orchestration or wave machinery, and require user approval of exact titles, title line arrangement, metric explanations, interaction affordances, and export paths before every plotting-code edit. Use when the user asks to plot, visualize, chart, make figures from results, or add or modify scripts under visualizations/; do not use to recompute missing evaluation data.
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
- Input: **`evaluations/` only**. Read the producer's recorded experiment-wide
  `RUN_ID_PATH_LAYOUT` and resolve inputs with
  `run_id_path(..., layout=RUN_ID_PATH_LAYOUT)` from the canonical helper. Never infer layout from
  slash depth or force `nested`. Viz never reads checkpoints or recomputes; if a quantity is absent
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

Rewriting a leaf whole is the expected result of every rerun. That self-overwrite needs no
approval while the resolved path is unchanged. Two different scripts resolving to the same leaf is
still an error: stop and propose a new leaf rather than letting one clobber the other.

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
  toggling natively. A different library for a specific plot is allowed only with explicit user
  approval through the plotting-communication gate; do not mix libraries silently within a project.
- **File size is not a constraint.** `plots/` is gitignored, so plots are shared by sending the
  file. Never propose committing plot output, and never downsample data to shrink a file.
- **Selection first, figure second.** The page lets the user choose among the run_id params the
  file covers, then renders the figure for that selection.
- **Offer the affordances the figure's shape earns**: a series toggle whenever it carries two or
  more line series, and a slider or stepper whenever it spans an ordered dimension such as layer,
  epoch, step, or checkpoint. Propose each one in the specification; the user approves or declines.
  Never impose a fixed interaction checklist beyond what the shape implies.

## Design & execution

- Before every creation or modification of plotting code, read the producer's authoritative
  ordered `RUN_ID_PARAMS`, `RUN_ID_PATH_LAYOUT`, metric definitions, evaluation schema, and
  scientific contract. Determine which RUN_ID params the figure covers and which of them the page
  lets the reader select.
- Propose one exact communication specification per plot: the title, the visible in-figure metric
  explanation, the interaction affordances, and the complete project-relative export path rooted
  at `plots/` and ending in its leaf filename. Explain what every plotted metric measures and its
  higher/lower/target/range/no-universal-direction interpretation, and state that text's exact
  placement.
- The title states **every** selected RUN_ID param as `key={value}`, so a figure is never
  ambiguous about which runs it shows. Titles are dynamic: they re-render with the selection.
  Propose the title template with every placeholder identified plus one fully resolved example.
- **Ask the user how the params are arranged across title lines** — which params share a line, in
  what order, and where the semantic part sits. This differs per plot. Never choose an arrangement
  silently, and never carry one over from another plot.
- Keep provenance in the file, since `plots/` is gitignored and the file travels alone: the
  selected run_ids in-figure, and the producer `<experiment_path>`, the project revision at render
  time, the render timestamp, and the evaluation source paths read in an unobtrusive page-level
  block. That metadata stays out of the title and must never crowd it.
- Dropping run_id segments normally makes the export path fully determined before the code edit,
  so propose the complete concrete path with the rest of the specification. If runtime values
  still prevent that, propose the complete path template and identify every placeholder
  explicitly. Before rendering, show every fully resolved concrete export path, including its leaf
  filename, and wait for explicit user approval. An approved template does not approve any
  concrete destination. A new or changed resolved path always reopens approval, even when it
  conforms to the approved template; do not render before that approval. Recheck cross-script
  collisions after resolution.
- Cover multiple axes, panels, derived metrics, and visual encodings separately unless one shared
  explanation is unambiguous. Stop rather than guess when metric semantics are not grounded. Put
  the explanation in an axis label, subtitle, legend, annotation, or in-figure caption; surrounding
  prose alone does not satisfy the requirement.
- Propose only. Wait for the user's explicit acceptance before editing plotting code.
  Re-propose and obtain approval on every later plotting-code edit even when the specification is
  unchanged. Neither scientific-auto nor engineering-auto may bypass this gate. An unchanged-code
  rerender may reuse approval only when every resolved concrete export path is byte-for-byte
  identical to the previously explicitly approved concrete path.
- After rendering, open the file and verify that the accepted text is present, legible, and
  unchanged in the page's default view, that every approved affordance actually works, and that
  the approved strings are present in the HTML source. Do not silently repair wording, placement,
  or title arrangement; propose any correction and reopen the approval gate before editing.
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
