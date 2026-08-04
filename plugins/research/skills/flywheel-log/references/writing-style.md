# Flywheel Writing Style

Flywheel node `content` and any empirical `summary.md` artifact should be dense,
scannable, and callout-driven.
The graph layer carries lineage and tags; the Markdown layer must carry the story.

This file is the complete and canonical style reference for any agent writing into
a Flywheel empirical or insight node. It owns both typography and Flywheel-specific
structure; no external formatting skill is required.

---

## Palette B (the only palette)

| Hex | Name | Use |
|---|---|---|
| `#540B0E` | deep red | rare — root-only emphasis, highlight text over cream |
| `#335C67` | blue-green | improvements, wins, lower loss, faster, smaller artifact |
| `#E09F3E` | amber | mixed results, partial wins, throughput penalties that aren't fatal |
| `#9E2A2B` | red | regressions, losses, slower, larger, worse |
| `#FFF3B0` | cream | background highlight (rare) |

The same Palette B controls the Flywheel **tag** colors defined by the
`flywheel-log` skill: `root` (`#540B0E`), `insight` (`#335C67`), and `experiment`
(`#E09F3E`). Do not introduce other colors. If a sixth category seems genuinely
needed, escalate to the user before adding one.

---

## The TL;DR callout (mandatory)

Every empirical node's `summary.md` and every insight node's canonical `content`
opens with one `> [!summary] TL;DR` callout right after the H1. This is the graph-level **context
preview** a reader sees before opening the node — its job is to convey *what this
node is about* well enough that the reader can tell it apart from its siblings,
not to reproduce the measurement table.

**Hard cap: 4 lines** (3 is preferred). Inside those 4 lines, include:

- the **method, system, or question** in human-language bold
- the **qualitative outcome** (does it win / lose / saturate / surprise) — not the
  precise number
- the main caveat in one breath, if any

If you cannot fit the claim in 4 lines, the claim is fuzzy — tighten the claim, do
not expand the box. No second paragraph, no bullets inside the callout, no nested
callouts.

### What never belongs in the TL;DR

The TL;DR is preview-layer prose; everything that requires opening the node to
decode is forbidden here. Move it to `## Setup` / `## Result` / `## Findings`.

- **Internal indices and short codes** — never `L0`, `T2`, `T6`, `ckpt_42`,
  `ckpt_2`, `comp_7`, `c_aug`, `K=5`, layer/timestep/checkpoint numbers,
  hyperparameter codes, run IDs, sweep coordinates.
- **Project-internal jargon** that a reader from a sibling branch wouldn't
  recognise. Spell things out: "an early SAE layer" instead of `L0`,
  "a mid-noise timestep" instead of `T2`, "a sparse-dictionary feature" instead
  of `comp_42`.
- **Detailed numbers** — exact val loss, exact deltas, raw counts. The TL;DR
  reports the *direction* of the result; the body reports the *magnitude*.
- **Verbose ablation tuples** — `(seed=42, lr=3e-4, K=5, T=6)`. Same rule:
  belongs in `## Setup`.

For an empirical node, the H1 mirrors the Flywheel title and the TL;DR restates
**what was measured and which way it came out**, qualitatively. The hypothesis is
an explicitly labeled section in node `content`; the measurement table lives in
`## Result`.
For an insight node, the TL;DR is the **claim itself**, in plain language, with
the parent experiments linked rather than re-summarised.

The callout label is the literal string `TL;DR` and the callout type is `[!summary]`.
No other label, no other callout type, no plain-text fallback.

Example (empirical):

```md
# E07 SiLU² Block Beats SiLU at Equal Time Budget

> [!summary] TL;DR
> Compares **SiLU² block** against a plain-SiLU block schedule at a fixed
> training-time budget. SiLU² wins consistently across the sweep, but the gain
> ==saturates once the budget is large enough== — extra compute stops paying off.
> Single seed; multi-seed confirmation pending.
```

Example (insight):

```md
# I05 Treat SiLU² Block as New SlowRun Candidate

> [!summary] TL;DR
> Argues that **SiLU² block** should replace plain SiLU as the default SlowRun
> activation. The case rests on the budget-matched comparison in [[E07]] and is
> contingent on a multi-seed re-run before being treated as load-bearing.
```

This is the first content a reader sees in the Flywheel UI. Spend the time.

---

## Callout vocabulary

Only the callouts below — they all render in Flywheel's Markdown renderer
(`rehype-callouts`) and across mirrored Obsidian notes. Do not invent new ones.

| Callout | When to use |
|---|---|
| `[!summary]` | "TL;DR" preview callout. Mandatory, one per node, ≤4 lines. |
| `[!info]` | Setup, dataset, hardware, budget, constraints. Bulleted. |
| `[!success]` | Leaderboard or top-N reveal. |
| `[!abstract]` | Head-to-head or matched-condition comparison. |
| `[!important]` | Headline finding restated as a one-line claim. |
| `[!warning]` | Honest caveat, trap, partial failure. |
| `[!danger]` | "Do NOT do X" — strong negative recommendation. |
| `[!tip]` | Actionable next-experiment suggestion. |
| `[!failure]` | Negative result, dead end, ablation that didn't help. |
| `[!hint]` | Optimisation opportunity, implementation note for later. |

Callouts are one idea deep. If you need two paragraphs you wanted prose.

---

## Highlight and bold conventions

- `**bold**` — method names, primary entities, column headers inside prose.
- `==highlight==` — the numbers a reader should pattern-match on later.
- `==**number**==` — the single most important number in a section.
- Inline color spans inside table cells when a cell carries a judgement:

```md
| **Val loss** | <span style="color:#335C67">**1.284**</span> | <span style="color:#9E2A2B">**1.327**</span> | <span style="color:#335C67">−0.043</span> |
```

Color spans only for *judgemental* cells. Not every cell needs colour — the goal is
that a reader scanning the table sees good vs. bad at a glance.

Mapping inside tables:

- `#335C67` — improvement, win
- `#9E2A2B` — regression, loss
- `#E09F3E` — mixed or partial wins

---

## Tables

- Numeric columns centred with `:---:`.
- Label columns left-aligned with `---`.
- Units in the header (`val loss`, `MB`, `ms / step`), not in every cell.
- Use `==…==` inside the winning cell *or* the baseline cell — pick one
  convention per table and stick to it.
- More than ~10 rows → use a plot instead.

---

## Section rhythm

`---` horizontal rules between sections. Omit any section that would be filler.

### Empirical (`summary.md`)

1. `# <Title — matches Flywheel node title>`
2. `> [!summary] TL;DR` (mandatory, ≤4 lines)
3. `## Setup` — with `> [!info] Constraints` (dataset, hardware, budget, metric)
4. `## Result` — headline metric, with table or plot reference
5. `## Findings` — numbered `###` subsections; figure first, then `> [!important]`
6. `## Caveats` — `> [!warning]` callouts if any honest caveats apply
7. `## Next Steps` — task-list `- [ ]`
8. `## Repro` — one-liner `See [[reproducibility.md]]` + commit SHA

### Insight (node body)

1. `# <Title — matches Flywheel node title>`
2. `> [!summary] TL;DR` (mandatory, ≤4 lines)
3. `## Claim` — one-paragraph restatement, optionally with `> [!important]`
4. `## Evidence` — bullets pointing at parent empirical nodes (`[[E07]]`-style
   references) with one-line summaries of what each contributes
5. `## Implication` — one paragraph on what changes next, ending with
   `> [!tip]` if a concrete follow-up experiment is implied

---

## Math

- Inline math in `$…$`.
- Display math in `$$…$$` on its own line.
- Don't wrap display math in a callout unless you're quoting it.

---

## Next Steps as task list

```md
- [ ] Concrete follow-up we could run
- [x] ~~Thing already tried~~ — one-line result
```

Checked items stay with strikethrough + one-line postmortem so the node becomes a
mini-log of what was explored.

---

## Tone

- Declarative, not promotional. *"The dense baseline remains unbeaten."* not
  *"Our exciting experiment shows…"*.
- Honest about negative results — devote a section to them when warranted.
- Default to passive or nominal constructions; first-person plural sparingly.
- The summary is a lab record, not a blog post.
