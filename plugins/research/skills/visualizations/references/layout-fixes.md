# Layout fixes for `plot_check.py` findings

`scripts/plot_check.py` in this skill's install directory opens a rendered plot in headless Chromium
at several window widths with the network blocked, measures the text Plotly drew (rotated boxes
included), checks the page contract, and saves full-page and per-figure screenshots. Each finding below names its usual cause and the Plotly change that removes it.
Fix the cause in the plotting script, re-render, and rerun the check; never nudge one pixel
offset until one width passes, since the next selection or window breaks it again.

Setup on a fresh machine (no root): `uv run --with playwright python -m playwright install
chromium-headless-shell`. Lightweight headless browsers without a layout engine (for example
Lightpanda) cannot do this job: they have no geometry and take no screenshots.

## Errors

| Finding | Usual cause | Fix |
|---|---|---|
| `text-clipped`: title runs past the figure edge | one long title line; `margin.t` too small | Break the title with `<br>` at param boundaries, put fixed params on a smaller second line (`<sup>` or `title.subtitle` in Plotly ≥ 5.23), `title.x=0.01, xanchor="left"`. Set `title.yref="container"` with `title.automargin=True` so Plotly reserves room (with `yref="paper"` automargin cannot keep it off the plot area). |
| `text-clipped`: tick or axis title | fixed margins | `xaxis.automargin=True`, `yaxis.automargin=True`; do not hard-set `margin.l`/`margin.b` below what labels need. |
| `text-over-data` (title) | `title.y` inside the paper area with a small top margin | `yref="container"` + `automargin=True`, or raise `margin.t`. |
| `text-overlap`: title / legend | legend anchored at the top over the title area | Legend to the right (default, `x=1.02`) or below the axis title: `orientation="h", yanchor="top", y=-0.2` **plus** enough `margin.b` for rotated ticks; the check catches ticks colliding with a bottom legend. |
| `text-overlap`: tick / legend or axis title / legend | bottom legend placed before rotated ticks were measured | Move the legend right, or shorten tick text (map long category names to short labels and explain them in hover text or a caption). |
| `legend-hides-data` | legend inside the plot area over traces | Legend outside (`x=1.02`), or into a corner the data never reaches; for many series, use the series toggle instead of a long legend. |
| `text-overlap`: annotation / anything | annotations at fixed paper coords | Anchor to data (`xref="x"`) with `ax/ay` offsets, or move the text to the caption. |
| `page-scrolls-sideways` | fixed-width HTML (tables, `<pre>`, selector rows) | `max-width:100%`, `overflow-wrap:anywhere` on long run lists, `flex-wrap:wrap` on control rows, `config={"responsive": True}` with no fixed `width`. |
| `html-text-clipped` | `text-overflow: ellipsis` / fixed width on labels | Let labels wrap; the full name is the point of the glossary. |
| `js-error` | broken inline data, script order | Fix before anything else: later findings are unreliable. |
| `no-figure` | nothing drawn within 15 s: a JS error, no data matching the filters, or the figure hidden until a click | Fix any `js-error` first. Draw from `DOMContentLoaded` or later with the default selection on; a figure that opens collapsed is never measured. |
| `text-off-window` | the figure is wider than the window, so its text runs past the edge | `config={"responsive": True}` and no fixed `layout.width`; let a wide table or grid wrap instead of pushing the figure out. |
| `legend-clipped` | a long legend, or one placed at fixed coordinates, runs past the figure | Legend to the right with `xanchor="left", x=1.02` and automargin on, or below the axis with room in `margin.b`; for many series, use the series toggle. |

## Page contract and offline errors

These read the rendered page against the skill's "The HTML file" section. Plots rendered before the
check covered them can fail here while looking fine; re-rendering from an up-to-date script fixes them.

| Finding | Usual cause | Fix |
|---|---|---|
| `network-request`, `external-reference` | a CDN or sibling-file `<script>`, `<link>` or image; Plotly exported with `include_plotlyjs="cdn"` | Inline everything: `include_plotlyjs=True`. The check blocks these requests, so what you see is what an offline reader sees. |
| `no-plot-leaf`, `no-plot-data`, `plot-data-invalid`, `plot-data-unescaped` | the page template lacks the contract's data | `<html data-plot-leaf="<leaf>.html">`; a `<script type="application/json" id="plot-data">` holding `{"runs": {<run_id>: ...}}`, written with `<` replaced by `\u003c`. |
| `no-selected-runs`, `selected-runs-invalid` | no selection function, or one returning something else | `window.plotSelectedRuns = () => [...]`, the run_ids in view, each a key of `runs`. |
| `no-controls-mark`, `no-provenance-mark` | the selection or provenance block is unmarked | Add `data-plot-controls` and `data-plot-provenance`; the toolbar and the download need them. |
| `not-white` | a Plotly template (the Python default paints the plot area blue-grey) or a dark-mode stylesheet | `paper_bgcolor="#ffffff"`, `plot_bgcolor="#ffffff"`, `body {background:#ffffff}`, and no `prefers-color-scheme` rules. |
| `asset-missing`, `asset-not-verbatim` | an asset left out or edited | Carry this skill's `assets/plot-download.js` and `assets/plot-shell.js` as constants in the plotting script, unchanged. |

## Warnings (look at the screenshot)

| Finding | Meaning | Fix when the screenshot confirms it |
|---|---|---|
| `no-title` | a figure drew no title | The skill requires one: `Full name = {value}` per selected param, or a plain semantic title with no fixed param. |
| `asset-outdated` | the inlined asset is an earlier release of this skill's; it still works | Re-copy the asset into the plotting script's constant at the next render. |
| `asset-order` | an asset is not in its place at the top of `<head>` | `plot-download.js` first, `plot-shell.js` second, both before Plotly. |
| `tick-crowding` | tick labels of one axis touch each other | `tickangle=-45` or `-90`, `nticks`, shorter category labels; on numeric axes let Plotly auto-thin (no explicit `tickvals` with every value). |
| `legend-over-data` | legend overlaps the plot area but no mark was found under it | Fine if it sits in empty space at every selection; else move it out. |
| `small-text` | text drawn under 10 px | `font.size` ≥ 12 for ticks, ≥ 13 for axis titles. |
| `cramped-plot-area` | plot area under 160×120 px | Fewer subplots per row, taller `height`, legend outside the subplot grid. |

## What the eye checks after the geometry passes

Read the per-figure screenshots (`<stem>.<width>.<state>.fig<N>.png`) at each width:

- dead bands of white (title far above the plot, huge margins);
- lines or markers hidden under others (opacity, draw order, small multiples instead);
- colors that are hard to tell apart or don't match the legend order;
- axis ranges wasting most of the plot, or a log scale where a linear one would read better (and the reverse);
- the title, axis text, and metric explanations present and saying what the skill requires.

## States worth checking

The title and legend change with the selection. Pass `--state NAME=JS` for selections that
lengthen the title (one value per param ticked) or shorten the legend (one run). Both paths are
absolute, since no single working directory holds both the script and the plot, e.g.

```bash
uv run <skill>/scripts/plot_check.py <project>/plots/.../leaf.html --state 'collapsed=document.querySelector("[data-plot-controls]").style.display="none"'
```
