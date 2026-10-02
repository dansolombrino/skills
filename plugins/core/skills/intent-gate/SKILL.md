---
name: intent-gate
description: Combined alignment and phase gate. Detect whether the user wants their intent mirrored back before any action, a brainstorm hold with no plan or code until an explicit greenlight, or both, and confirm each mode separately before applying it. Use when a message says things like "show me you get me and don't do anything yet", "confirm alignment, then we brainstorm, no plan until I say go", or any mix of "prove you understood", "no planning yet", "wait for my greenlight". Do not use for ordinary requests that merely mention brainstorming or understanding in passing.
---

# intent-gate

One entry point for both gates. Canon: [`references/contract.md`](references/contract.md); this body
summarizes it and never overrides it. `intent-mirror` and `brainstorm-hold` are the same two modes
exposed as single skills; behavior is identical whichever skill fired.

This skill constrains output and action, not reasoning. Engineering-auto may not bypass this gate.

## 1. Detect the modes present

- **Mirror**: the user wants their intent reflected back and confirmed before any action.
- **Hold**: the user wants discussion only, with no plan, step list, or code until a greenlight.
- A message may carry one mode or both. Do not add a mode the user did not ask for.

## 2. Confirm once per mode

- Ask one separate confirmation per detected mode, each in the shape "I read this as a request
  to **<mode>** (...). Did I sniff that right?", and wait for each answer.
- Never bundle the two confirmations into one question. The user keeps control of each mode
  independently.
- Produce nothing else until every detected mode has been answered. A "no" cancels only that mode.

## 3. Apply mirror, then hold

- If mirror is active, send the five-part mirror first: **Intent**, **Goal of the chat**,
  **Constraints**, **Open questions**, **What I am not doing yet**. No action in that message.
- If hold is active, negotiate exploration next: "Do you want exploration at this stage?" and,
  if yes, what it may and may not consider.
- Then hold: no plans, step lists, sketches, code, edits, tool writes, or the host's planning
  mode. Opinions, options, tradeoffs, recommendations, and questions stay allowed.

## 4. Persist, re-fire, release

- Mirror re-fires on every later directive that would lead to action, for the rest of the chat.
- Hold persists until a greenlight: the user's explicit release by intent. Agreement on a detail,
  "ok" answering a question, or enthusiasm is not release. When unsure, ask "Is that a
  greenlight?"
- On greenlight, state what is unlocked (planning only, or planning and implementation) and ask
  if unclear. Each posture ends only on a greenlight or an explicit directive to stop it.
