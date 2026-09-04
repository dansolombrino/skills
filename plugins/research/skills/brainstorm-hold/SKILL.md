---
name: brainstorm-hold
description: Hold a discussion in brainstorm phase and produce no plan, step list, or code until the user gives an explicit greenlight. Use when the user says things like "brainstorm only", "no planning yet", "don't implement anything", "wait for my greenlight / go / ok before you plan", "let's lock the details first", or "this is a discussion phase". Exploration is negotiated, not assumed. Do not use for ordinary requests that merely mention brainstorming as background, and do not use when the user asks to plan or implement now; use intent-mirror for pure alignment checks.
---

# brainstorm-hold

Discuss freely, produce no plan. Canon: [`../intent-gate/references/contract.md`](../intent-gate/references/contract.md);
this body summarizes it and never overrides it. `intent-mirror` and `intent-gate` share the same
contract, so their behavior is identical wherever they overlap.

This skill constrains output and action, not reasoning. Engineering-auto may not bypass this gate.

## 1. Confirm the trigger

- First message: name the mode and ask. "I read this as a request to **hold** in brainstorm
  phase until you greenlight the next step. Did I sniff that right?" Produce nothing else.
- A "no" cancels the skill. Continue with the user's actual request.

## 2. Negotiate exploration

- Ask: "Do you want exploration at this stage?" Never assume either answer.
- If yes, ask what exploration may and may not consider, and stay inside that boundary.
- Re-ask when the stage changes. Report findings as facts and questions, never as a plan.

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
