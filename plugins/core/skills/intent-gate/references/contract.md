# Intent gate contract

Shared canon for `intent-mirror`, `brainstorm-hold`, and `intent-gate`. The three skills are one
behavior exposed through three triggers; every definition below applies identically to all of
them. When a skill body and this file disagree, this file wins.

These gates constrain **what is produced and when action happens**. They never constrain how the
model reasons. Think freely; hold the output.

## Trigger confirmation

The first message after any of these skills fires does exactly one thing: it names the mode that
fired and asks whether the user's intent was read correctly. Use this shape, adapted to the
user's words:

> I read this as a request to **<mode>** (<one-line paraphrase of what that means here>).
> Did I sniff that right?

- One confirmation per mode. If two modes fired, ask two separate questions, each awaiting its
  own answer. Never bundle them.
- Produce nothing else in that message: no mirror, no options, no exploration, no action.
- A "no" cancels that mode. A "yes" activates it. An ambiguous reply is a question, not a yes:
  ask again in fewer words.

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

## Exploration negotiation

Exploration means reading files, searching, or inspecting state to sharpen the discussion.
It is never assumed on and never assumed off.

- After the mode is confirmed, ask: "Do you want exploration at this stage?"
- If yes, ask what it may and may not consider, and stay inside that boundary.
- If the stage changes (a new topic, a partial greenlight), ask again.
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

- Once `intent-mirror` is confirmed, it is a posture for the rest of the chat: every later
  directive that would lead to action gets a fresh mirror before anything is done. This includes
  directives issued after a greenlight.
- Once `brainstorm-hold` is confirmed, it persists until a greenlight lifts it. A new topic does
  not lift it.
- Both end only on an explicit user directive to stop, such as "stop mirroring" or "no more
  holds". Stopping is itself a directive: confirm it in one line before dropping the posture.

## Precedence

These gates are always-protected. In a research project with an execution agreement, the
`engineering_mode` value does not matter here: Engineering-auto may not bypass this gate. Any
other skill that would act, plan, or edit defers to an active mirror or hold.
