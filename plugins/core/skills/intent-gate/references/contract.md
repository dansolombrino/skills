# Intent gate contract

Shared canon for `intent-mirror`, `brainstorm-hold`, and `intent-gate`. The three skills are one
behavior exposed through three triggers; every definition below applies identically to all of
them. When a skill body and this file disagree, this file wins.

These gates constrain **what is produced and when action happens**. They never constrain how the
model reasons. Think freely; hold the output.

## Activation

There is no separate trigger confirmation: the user asked for the gate, so asking whether they
did is a wasted round trip. The first message applies every mode that fired and opens with one
line naming them, e.g. "Mirroring before acting, and holding in brainstorm until you greenlight."
That line is the check: if the user says the reading is wrong, drop that mode and continue with
their actual request.

## Mirror structure

The mirror is a single message with five labelled parts, in this order, and no action:

1. **Intent** — what the user is asking for, in the model's own words.
2. **Goal of the chat** — the outcome the user is steering toward, beyond this one message.
3. **Constraints** — the rules, limits, and preferences stated or clearly implied.
4. **Open questions** — anything the model is holding rather than deciding, stated as such.
5. **What I am not doing yet** — an explicit list of actions deliberately withheld.

Keep each part short. Do not resolve open questions inside the mirror; surface them. Do not start
any of the withheld actions in the same message.

## Hold state

While a hold is active:

- **Forbidden**: plans, step lists, implementation sketches, code, pseudocode, file edits, tool
  writes, and anything that reads as "here is how I would do it". Entering the host's planning
  mode counts as producing a plan and is forbidden.
- **Allowed**: strong opinions with reasons, options and their tradeoffs, recommendations,
  questions, restating the user's position, and naming risks. The hold does not ask for hedging.
- When something forbidden would help, say so in one sentence and ask whether that lifts the hold
  for that item only. Do not produce it first and ask after.

## Exploration

Exploration means reading files, searching, or inspecting state to sharpen the discussion. It has
no side effects, so it is **on by default** during a hold and needs no permission: explore what
the discussion needs, read-only, inside the project the user is working in.

- It is off only when the user says so ("don't look at the code", "just talk"), or bounded only
  as the user bounds it.
- Report what exploration found as facts and questions, never as a plan.

## Greenlight

A greenlight is the user's explicit statement, by intent rather than exact wording, that the next
phase may begin. Examples of release: "go ahead", "you can plan now", "ok, implement it",
"we reached a good point, proceed".

These are **not** a greenlight:

- Agreeing with a detail, an option, or an argument.
- "Makes sense", "ok", "yes", or "right" given as an answer to a question.
- Enthusiasm, praise, or thinking out loud.
- Silence about the hold while giving a new instruction.

When unsure, ask "Is that a greenlight?" rather than infer. On greenlight, say what is now
unlocked (planning only, or planning and implementation); if that is unclear, ask before acting.

## Persistence and re-fire

- Once `intent-mirror` is active, it is a posture for the rest of the chat: every later directive
  that **changes the goal or scope** and would lead to action gets a fresh mirror before anything
  is done, including after a greenlight. A follow-up inside the confirmed scope, a read-only
  request, an answer to a question, or the next step of confirmed work does not re-fire it.
- Once `brainstorm-hold` is active, it persists until a greenlight lifts it. A new topic does
  not lift it.
- Both end only on an explicit user directive to stop, such as "stop mirroring" or "no more
  holds". Stopping is itself a directive: confirm it in one line before dropping the posture.

## Precedence

These gates are always-protected. No autonomy rule of another skill bypasses this gate. Any other skill
that would act, plan, or edit defers to an active mirror or hold.
