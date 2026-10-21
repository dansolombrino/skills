---
name: intent-mirror
description: Reflect the user's intent back and take no action until they confirm alignment. Use when the user says things like "show me that you get me first", "prove you understood before doing anything", "just confirm alignment", "restate what I'm asking", "make sure we're on the same page before acting", or "do nothing else yet, just show me you get it", at the start of a chat or before acting on any new directive. Once active it re-fires on every later directive that changes the goal or scope. Do not use when the user asks to plan, implement, or explore; use brainstorm-hold or intent-gate for hold requests.
---

# intent-mirror

Show understanding before doing anything. Canon: [`../intent-gate/references/contract.md`](../intent-gate/references/contract.md);
this body summarizes it and never overrides it. `brainstorm-hold` and `intent-gate` share the
same contract, so their behavior is identical wherever they overlap.

This skill constrains output and action, not reasoning. No autonomy rule of another skill bypasses this gate.

## 1. Emit the mirror

No trigger confirmation: the user asked for the mirror. Send it right away, opening with one line
("Mirroring before acting."), as one message with exactly these five labelled parts and no action:

1. **Intent**
2. **Goal of the chat**
3. **Constraints**
4. **Open questions** — held, not decided.
5. **What I am not doing yet** — the actions deliberately withheld.

Do not resolve open questions inside the mirror. Do not begin any withheld action.

## 2. Wait

Stop after the mirror. Act only when the user confirms the reading or corrects it. A correction
produces a revised mirror, not an action.

## 3. Re-fire on a new direction

The mirror is a posture for the rest of the chat. Each new directive that changes the goal or
scope and would lead to action gets a fresh mirror first, including after a greenlight. A
follow-up inside the confirmed scope, a read-only request, an answer to a question, or the next
step of confirmed work does not re-fire it.

## 4. Stop only on an explicit directive

Drop the posture only when the user says to stop mirroring. Confirm that in one line, then stop.
