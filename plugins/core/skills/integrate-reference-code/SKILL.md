---
name: integrate-reference-code
description: Integrate, port, reproduce, or adapt local, supplied, repository, or online reference source code into an existing codebase while requiring explicit user approval of every behavioral source-to-target deviation before editing; mechanical translation is done and reported.
---

# integrate-reference-code

Integrate reference implementations without silent drift. Retain full freedom to propose the
best target-native implementation, but make every departure from the reference visible, and get
every behavioral departure user-approved before writing it.

In a Research 2.0 project (its `.project.toml` lists `research`), require that scaffold first;
in any other project, integrate into the existing codebase as it stands. Behavioral deviations
are an always-protected choice: no autonomy rule approves them, and the user's row-level
approval remains required. Mechanical deviations are made and logged without asking.

References are always user-supplied: the user names each one by path or URL, or places it in the
project's `references/` directory. This skill is the only sanctioned path for external source code
entering a project, and it never authorizes surveying the filesystem for candidates. Do not go
looking for a similar project to adapt, and do not widen an approved reference to its neighbours
on disk.

## 1. Inspect before editing

1. Identify each reference by local path or URL and by commit, tag, version, or retrieval date
   when available.
2. Inspect the reference and the relevant target code, interfaces, conventions, and tests.
3. For external code, identify its license and attribution requirements. Surface missing,
   unclear, or incompatible reuse terms; do not copy code until the reuse basis is clear.
4. Determine whether the reference can be integrated exactly.

Keep this phase read-only. Do not edit tracked files, run formatters or code generation that
rewrites them, or begin the implementation before the deviation gate is resolved. If the
reference is unavailable, incomplete, ambiguous, or conflicts with another reference, report
the problem and ask the user to resolve it.

## 2. Build the deviation ledger

Treat a **deviation** as any source-to-target difference, and classify each one:

- **Mechanical** — changes the shape, not what the code computes: names, file layout, imports,
  formatting, wiring to the target's existing APIs, config plumbing that keeps the reference's
  values, logging, and tests. Make these freely; list them in the ledger as `done` and report them.
- **Behavioral** — anything that can change what the code computes or how it fails: control flow,
  defaults, hyperparameter or config values, data representations, numerical behavior, precision,
  randomness, error handling, performance trade-offs, and new or different dependencies. These
  need the user's approval. When unsure which kind a change is, treat it as behavioral.

If any behavioral deviation is needed, present one consolidated ledger before editing:

| Reference behavior | Target constraint | Proposed deviation | Reason | Consequence or risk | Validation | Approval |
|---|---|---|---|---|---|---|

Use all seven columns without merging or omitting fields, and mark each behavioral row's initial
approval as `pending` and each mechanical row as `mechanical — done`. Give each row its own rationale and validation method. Enumerate every concrete
transformation. Group the discussion into one approval request, but do not hide individual
changes behind a broad category such as "project integration." Distinguish faithful copying,
mechanical translation, and intentional adaptation.

If no behavioral deviation is needed, identify the source, state that the integration is exact
or mechanical only, list the mechanical rows, and proceed without inventing an approval gate.

## 3. Obtain informed approval

- Stop and wait for the user to approve, reject, or revise the behavioral rows.
- Treat general permission such as "adapt as needed" as implementation freedom, not approval of
  undisclosed deviations.
- Implement only approved behavioral rows, plus mechanical ones. Do not infer approval for
  adjacent or similar behavioral changes.
- Record the decision in the conversational ledger unless the user requests a durable artifact.

## 4. Implement within the approval

Choose the best implementation freely within the approved direction. Preserve reference
behavior everywhere the ledger does not authorize a difference.

If a new deviation becomes necessary, stop before applying it. A mechanical one is added to the
ledger as done and reported, with no stop. For a behavioral one, report what is already
complete, add the new row to the ledger, and reopen discussion. Continue only after that row is
approved.

## 5. Verify and reconcile

Run tests that compare the implemented behavior with both the reference and the approved target
behavior. In the final report:

- identify the reference and revision used;
- map every approved deviation to the resulting code and validation evidence;
- distinguish faithful, mechanically translated, and intentionally adapted portions;
- disclose unresolved fidelity gaps, risks, or licensing/attribution obligations; and
- state whether any unapproved behavioral deviation remains. Never describe an adapted implementation as an
  exact reproduction.
