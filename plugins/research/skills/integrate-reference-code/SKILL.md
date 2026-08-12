---
name: integrate-reference-code
description: Integrate, port, reproduce, or adapt local, supplied, repository, or online reference source code into an existing codebase while requiring explicit user review and approval of every source-to-target deviation before editing.
---

# integrate-reference-code

Integrate reference implementations without silent drift. Retain full freedom to propose the
best target-native implementation, but make every departure from the reference visible and
user-approved before writing it.

In a research project, require the Research 2.0 scaffold first. Source-to-target deviations are an
always-protected choice: neither scientific-auto nor engineering-auto may approve them. The user's
row-level approval remains required regardless of the active modes.

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

Treat a **deviation** as any source-to-target difference, including mechanical changes to names,
file layout, APIs, dependencies, configuration, data representations, control flow, defaults,
numerical behavior, error handling, tests, performance, or precision.

If any deviation is needed, present one consolidated ledger before editing:

| Reference behavior | Target constraint | Proposed deviation | Reason | Consequence or risk | Validation | Approval |
|---|---|---|---|---|---|---|

Use all seven columns without merging or omitting fields, and mark each initial approval as
`pending`. Give each row its own rationale and validation method. Enumerate every concrete
transformation. Group the discussion into one approval request, but do not hide individual
changes behind a broad category such as "project integration." Distinguish faithful copying,
mechanical translation, and intentional adaptation.

If no deviation is needed, identify the source and explicitly state that an exact integration
appears possible; then proceed without inventing an approval gate.

## 3. Obtain informed approval

- Stop and wait for the user to approve, reject, or revise the ledger.
- Treat general permission such as "adapt as needed" as implementation freedom, not approval of
  undisclosed deviations.
- Implement only approved rows. Do not infer approval for adjacent or similar changes.
- Record the decision in the conversational ledger unless the user requests a durable artifact.

## 4. Implement within the approval

Choose the best implementation freely within the approved direction. Preserve reference
behavior everywhere the ledger does not authorize a difference.

If a new deviation becomes necessary, stop before applying it. Report what is already complete,
add the new row to the ledger, and reopen discussion. Continue only after that row is approved.

## 5. Verify and reconcile

Run tests that compare the implemented behavior with both the reference and the approved target
behavior. In the final report:

- identify the reference and revision used;
- map every approved deviation to the resulting code and validation evidence;
- distinguish faithful, mechanically translated, and intentionally adapted portions;
- disclose unresolved fidelity gaps, risks, or licensing/attribution obligations; and
- state whether any unapproved deviation remains. Never describe an adapted implementation as an
  exact reproduction.
