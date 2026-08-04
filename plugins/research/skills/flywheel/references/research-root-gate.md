# Research Root Gate

Run this gate before any Flywheel graph mutation, edge change, import, execution,
compute acquisition, or artifact attachment in a Research 2.0 project.

Every new Research 2.0 node must be created with `flywheel_branch_node` (or the CLI
branch equivalent) from the canonical root or another ancestry-verified parent.
`flywheel_commit_new_node` creates an unattached node and is therefore reserved for
non-Research use or an explicitly approved creation of a new root.

## Resolve the Canonical Root

Use the first workspace source that resolves:

1. `./.flywheel.json` -> `rootNodeId`
2. `./.env` -> `FLYWHEEL_ROOT_NODE_ID`, or `FLYWHEEL_ROOT_NODE_SLUG` when no id is present
3. VS Code setting `flywheel.defaultRootNodeId`
4. Ask the user

An explicit node id, slug, focused node, or recently referenced node may identify a
starting point, parent, or source. It never overrides the workspace root. If `.env`
exists but its Flywheel root keys are missing or contradictory, stop and report
setup-incomplete state rather than guessing.

Confirm authentication, resolve any slug unambiguously, and fetch the canonical
root before proceeding. Quote `FLYWHEEL_ROOT_NODE_TITLE` when available so a human
can notice a configuration mismatch.

## Verify Every Governing Node

For every proposed start, parent, source, control, or import-target node:

1. Fetch the node.
2. Call `flywheel_get_node_ancestry` in MCP mode, or
   `flywheel nodes:render:ancestry` in CLI mode.
3. Require the resolved canonical root id to appear at that ancestry boundary.

Run the check before the first write and repeat it for any newly introduced
governing node. Recent context, explicit ids, graph search results, and user-facing
titles do not bypass ancestry verification.

If a candidate belongs to another root, stop and report the mismatch. Re-parenting,
cross-root import, root replacement, and mutation outside the approved root are
protected actions and require explicit user authorization plus a revised execution
agreement where applicable.
