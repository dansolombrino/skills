---
name: intent-mirror
description: Reflect the user's intent back and take no action until they confirm alignment. Use when the user says things like "show me that you get me first", "prove you understood before doing anything", "just confirm alignment", "restate what I'm asking", "make sure we're on the same page before acting", or "do nothing else yet, just show me you get it", at the start of a chat or before acting on any new directive. Once active it re-fires on every later directive. Do not use when the user asks to plan, implement, or explore; use brainstorm-hold or intent-gate for hold requests.
---

# intent-mirror

Show understanding before doing anything. Canon: [`../intent-gate/references/contract.md`](../intent-gate/references/contract.md);
this body summarizes it and never overrides it. `brainstorm-hold` and `intent-gate` share the
same contract, so their behavior is identical wherever they overlap.

This skill constrains output and action, not reasoning. Engineering-auto may not bypass this gate.

## 1. Confirm the trigger

- First message: name the mode and ask. "I read this as a request to **mirror** your intent
  before acting. Did I sniff that right?" Produce nothing else in that message.
- A "no" cancels the skill. Continue with the user's actual request.

## 2. Emit the mirror

On "yes", send one message with exactly these five labelled parts and no action:

1. **Intent**
2. **Goal of the chat**
3. **Constraints**
4. **Open questions** — held, not decided.
5. **What I am not doing yet** — the actions deliberately withheld.

Do not resolve open questions inside the mirror. Do not begin any withheld action.

## 3. Wait

Stop after the mirror. Act only when the user confirms the reading or corrects it. A correction
produces a revised mirror, not an action.

## 4. Re-fire on every later directive

The mirror is a posture for the rest of the chat. Each new directive that would lead to action
gets a fresh mirror first, including directives that follow a greenlight. Confirmation of the
posture happens once; the mirror itself repeats.

## 5. Stop only on an explicit directive

Drop the posture only when the user says to stop mirroring. Confirm that in one line, then stop.
