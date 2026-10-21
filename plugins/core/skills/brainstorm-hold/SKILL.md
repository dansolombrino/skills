---
name: brainstorm-hold
description: Hold a discussion in brainstorm phase and produce no plan, step list, or code until the user gives an explicit greenlight. Use when the user says things like "brainstorm only", "no planning yet", "don't implement anything", "wait for my greenlight / go / ok before you plan", "let's lock the details first", or "this is a discussion phase". Read-only exploration is on unless the user turns it off. Do not use for ordinary requests that merely mention brainstorming as background, and do not use when the user asks to plan or implement now; use intent-mirror for pure alignment checks.
---

# brainstorm-hold

Discuss freely, produce no plan. Canon: [`../intent-gate/references/contract.md`](../intent-gate/references/contract.md);
this body summarizes it and never overrides it. `intent-mirror` and `intent-gate` share the same
contract, so their behavior is identical wherever they overlap.

This skill constrains output and action, not reasoning. No autonomy rule of another skill bypasses this gate.

## 1. Start holding

No trigger confirmation: the user asked for the hold. Open the first reply with one line
("Holding in brainstorm until you greenlight.") and go straight into the discussion. If the user
says that reading is wrong, drop the hold.

## 2. Explore read-only by default

- Read files, search, and inspect state as the discussion needs, without asking: it has no side
  effects. Stop or narrow only when the user says so.
- Report findings as facts and questions, never as a plan.

## 3. Hold

- **Forbidden**: plans, step lists, implementation sketches, code, pseudocode, file edits, tool
  writes, and the host's planning mode.
- **Allowed**: strong opinions with reasons, options and tradeoffs, recommendations, questions,
  risks, and restating the user's position. Do not hedge because of the hold.
- If something forbidden would help, say so in one sentence and ask whether it is unlocked for
  that item only. Never produce it first.
- End discussion messages with a one-line "what I am not doing yet" so the hold stays visible.

## 4. Recognize the greenlight

- A greenlight is the user's explicit release by intent, not by exact words.
- Not a greenlight: agreeing with a detail, "makes sense" or "ok" answering a question,
  enthusiasm, or a new instruction that does not mention the hold.
- When unsure, ask "Is that a greenlight?" rather than infer.

## 5. Lift the hold

On greenlight, state what is now unlocked: planning only, or planning and implementation. If
that is unclear, ask before acting. The hold persists across topic changes and ends only on a
greenlight or an explicit directive to drop it.
