# REPORT 2026-07-22 — Harness pattern inventory

## What this is

Roadmap §7 item 3 (`docs/ROADMAP.md`): "map your existing guards to named
patterns — inventory stuck-limit / no-progress / confirm-first against the
studied shapes and see how much 'agent harness' is actually left to write."
Pure inventory, no code changes.

## The studied shapes (roadmap §6)

The roadmap names five: max-iteration caps, failure-threshold escalation,
explicit termination, plan-and-execute vs ReAct, state-machine control.

## The inventory

`agent_loop_holo.py`'s entire guard surface is five named constants — nothing
else in the loop implements a limit, timeout, or abort condition:

| Constant | Value | Trigger | Pattern |
|---|---|---|---|
| `max_steps` | caller-supplied (default 10) | step counter reaches the cap | **max-iteration cap** — textbook, exact match |
| `STUCK_LIMIT` | 3 | consecutive dropped/error steps | **failure-threshold escalation** — k-strikes, escalation destination = abort |
| `NO_PROGRESS_LIMIT` | 4 | consecutive executed steps with no visible screen change, OR the same click repeated | **failure-threshold escalation**, same family as `STUCK_LIMIT` but keyed on *outcome* stagnation rather than an execution error |
| `GUARD_REFUSE_LIMIT` | 3 | consecutive TOCTOU pre-fire guard refusals | **failure-threshold escalation**, same family again, deliberately counted separately from `STUCK_LIMIT` (a refusal isn't a model failure) |
| `CONFIRM_FIRST` | 5 | step index < N | **human-in-the-loop gate** — not one of the roadmap's five, but an established pattern (LangChain's approval step, AutoGPT's continuous-mode toggle); a staged/canary launch, not a failure response |

Two of the five studied shapes exist but aren't guard *constants* — they're
structural:

- **Explicit termination** — already present as the model's own `finished`/
  `answer` tool call (native's own termination primitive, not something this
  harness invented). The loop treats `answer_text is not None` as the only
  non-abort exit.
- **State-machine control** — informally yes: `stuck` / `frozen` /
  `click_repeat` / `guard_refusals` are real per-run state, reset at the right
  boundaries (run start, on a clean step). But there's no explicit state enum
  or transition table — it's four independent counters, not a modeled machine.

One studied shape is **not adopted**: **plan-and-execute vs ReAct.** The loop
is single-loop ReAct (observe → think → act, every step) with a bolt-on
`update_plan` bookkeeping tool that's explicitly decorative — telemetry, not
control (`docs/ROADMAP.md` §2). There is no separate planner/executor split.
This is exactly what roadmap Phase 2 (subgoal-gated loop) would introduce.

## Verdict

**Less than the phrase implies, as the roadmap itself predicted.** The
"agent harness" is: one iteration cap, three instances of the same k-strikes
circuit breaker (differing only in what they count), one human-confirmation
gate, and the model's own termination call. Four independent counters
standing in for a state machine, and a ReAct loop with no planner. Nothing
here is missing a name — it was already reinvented correctly, just not
labeled. The only real gap against the five studied shapes is
plan-and-execute, and closing that gap *is* Phase 2, not a naming exercise.

No code changes from this report. §7 item 3 is closed.
