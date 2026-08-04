---
name: flywheel-index
description: Build or refresh a local index.md mirror of a Flywheel research graph by resolving the canonical root, enumerating nodes, preserving their TLDRs, and reconciling missing or drifted entries. Use when the user asks to rebuild, update, sync, or inspect the local Flywheel index; do not treat index.md as authoritative graph state.
---

# Flywheel Local Index Builder

Support both installed Flywheel modes. Use the MCP tools named below when MCP is
available; in CLI mode, load `$flywheel`'s CLI tool map and use the equivalent
`flywheel` command. Never guess unavailable graph fields.

## Arguments

- No flag → **incremental** mode: add nodes that are in Flywheel but missing from
  `index.md`, and flag drift (entries whose outcome/TL;DR changed).
- `--rebuild` → **full** mode: discard the current entry list and regenerate every
  entry from the graph (the H1 header and intro line are preserved/recreated).
- An optional explicit root node id may resolve an otherwise unconfigured project. It must match an
  existing canonical workspace root; never use it to silently override one.

## Scope and Source of Truth

This skill is the procedure for rebuilding or reconciling `index.md` from the
Flywheel graph. `$flywheel-log` defines the canonical root-resolution and one-entry
index contract; the full rebuild mechanics and concrete format live below. If the
entry fields drift, `$flywheel-log` wins. `index.md` is a *mirror*: Flywheel is
authoritative, this file is the fast local lookup. Never invent content that is not
in the graph.

## Step 0 — Resolve the Project Root

Identify the canonical root node, in this order (mirror the Flywheel VS Code extension):

1. `./.flywheel.json` → `rootNodeId`
2. `./.env` → `FLYWHEEL_ROOT_NODE_ID` (UUID, preferred) or `FLYWHEEL_ROOT_NODE_SLUG`;
   quote `FLYWHEEL_ROOT_NODE_TITLE` back to the user so they can catch a mismatch
3. VS Code setting `flywheel.defaultRootNodeId`
4. An explicit root id passed as an argument, only when no workspace root resolved

If none resolve, **ask the user** before reading the graph — do not guess a root.
If an explicit id conflicts with a workspace root, stop and report the mismatch.
Confirm auth first with `flywheel_auth_status`.

## Step 1 — Enumerate the Graph

Call `flywheel_get_node_tree` (or `flywheel_list_nodes`) from the resolved root to get
every node's id and available core metadata. Keep the full list. Do not assume tree
projections carry body-derived semantic class, outcome, or TL;DR fields.

## Step 2 — Diff Against the Existing index.md

Read `index.md` in the project root if it exists. Parse the node ids already present
(the back-ticked id on each entry's metadata line). Compute:

- **missing** = graph nodes whose id is not in `index.md`
- **drift candidates** = all graph nodes already present in the local index

In incremental mode, fetch current node bodies for every missing node and every node
already present in the index, then compare exact title, body-derived semantic class,
body-derived outcome, and TL;DR. This full body comparison is required because the
tree enumeration cannot prove that a local entry is unchanged. In `--rebuild` mode,
fetch every node and regenerate every entry.

## Step 3 — Read Each TL;DR Verbatim

For each fetched node, call `flywheel_get_node` and extract the **verbatim** TL;DR from
the body — the text inside the `> [!summary] TL;DR` callout (strip the `> ` prefixes and
the `[!summary] TL;DR` label, keep `**bold**`/`==highlight==` markup as-is). If a node
has no TL;DR callout, record the entry with the marker `⚠ TL;DR missing` and a one-line
description derived from the title; do not fabricate a result. Derive `kind` and
`outcome` only from explicitly labeled semantic metadata in `content` (for example,
`Record type: empirical` and `Outcome: failed`); otherwise use `untyped` and `—`.

## Step 4 — Write index.md

Emit the canonical format below, **newest first** (sort entries by `created_at`
descending). Preserve the H1 + intro line; replace or append entries per mode. Then
report: how many entries added, how many drifted (and were refreshed), and any nodes
flagged `⚠ TL;DR missing`.

## Canonical Format

```markdown
# Flywheel Node Index — <project>

Local mirror of the Flywheel graph. The authoritative record lives in Flywheel;
this is the fast local lookup. Regenerate with `$flywheel-index`.

## <Node Title>
`<node-id>` · <kind> · <outcome> · <date>
> TL;DR: <verbatim TL;DR text, single blockquote>

## <Next Node Title>
`<node-id>` · <kind> · <outcome> · <date>
> TL;DR: <…>
```

Rules for an entry:
- H2 title is the exact node title.
- Metadata line: back-ticked node id, then `kind` (`empirical`/`insight`/`untyped`),
  `outcome` (`completed`/`failed`/`canceled`/`—`), and the node's date, joined by ` · `.
- One blockquote line `> TL;DR: …` carrying the verbatim TL;DR (collapse a multi-line
  callout into one blockquote line).
- Nothing else per entry — the full durable record stays in node `content`; any
  `summary.md` is only an optional supporting artifact.
