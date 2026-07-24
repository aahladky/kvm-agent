# SESSION 2026-07-23 — Phase 2 slice D-b: rig results (the numbers D-c/D-d need)

## What this session was

The one rig session `docs/SESSION_2026-07-23_phase2_slice_d_b_shadow_wiring.md` left
outstanding: extended GNOME battery, `verify_mode="shadow"`, human still grading.
Operator merged `slice-d-b-shadow-wiring` to main (PR #4, `4b9405a`) and ran it:

```
python tools/battery.py tools/battery_tasks_gnome.json shadow
python tools/battery_metrics.py
```

Evidence: `runs/battery_20260723_093442/results.json` (10 per-task run dirs,
`runs/battery_<task_id>_20260723_*/`), aggregated at
`runs/battery_metrics_20260723_100508/report.json`.

## The numbers

- **Completion: 10/10 human-graded pass, 0 voids.** Read plainly, this IS another
  ceiling sweep — the harder tasks did not knock the model off 100% at the grade level.
  The one interesting case (`copy_paste_notes`, below) is a `finished=False` run the
  human still graded pass, not a graded failure.
- **False-"finished" rate: 0/9 (0.0%).** Every run that claimed `finished=True` was
  graded a true pass. Phase 2's headline failure mode — confident-wrong progress — did
  not occur on this battery. Zero true-fail cases means false-confirmation is also
  **unmeasured, not zero**: there was nothing wrong for the verifier to wrongly bless.
- **Verifier agreement, as `battery_metrics.py` defines it: 9/9 comparable, 100%,
  false-refusal 0/9 (0.0%).** This denominator is *only* the 9 runs where the verifier
  produced a verdict (`auto_grade is not None`) — **`copy_paste_notes` is excluded, not
  counted as an agreement.** That exclusion matters: the run never called `finished`, so
  no verdict was ever computed, yet the human graded it pass. Under D-c's planned
  auto-grading default (a missing/`None` verdict grades fail, fail-closed by design),
  this task would auto-grade **fail** against a human **pass** — a real auto/human
  divergence that today's 9-comparable denominator doesn't surface. Counted against all
  10 tasks, agreement is **9/10**, not 100%. **False-refusal 0/9 is still the number
  that gates D-c** (it only requires the verifier not to wrongly reject a *true* pass it
  actually judged) — that gate clears — but "both gates clear" overstates what was
  measured; see below.
- **Guard-refusal rate: 8/76 steps (10.5%)** — the pre-fire TOCTOU guard is firing more
  on these harder tasks than the 4/64 (6.25%) baseline from the simpler five-task
  battery (`docs/SESSION_2026-07-22_toctou_guard_rig_confirmation.md`), consistent with
  more multi-window/dialog-heavy tasks giving it more chances to catch a stale-tile fire.
  No run was lost to it (`GUARD_REFUSE_LIMIT` never hit).
- **Latency**: actor median 15.8s/step (range 6.6–28.7s), verify median 4.9s/step
  (range 4.3–5.4s, tight as expected for a short stateless call), combined
  finished-step median 26.6s. Confirms the `--parallel 1` serialization assumption in
  `battery_metrics.py`'s own docstring — verify adds real, not overlapped, latency.
- **`update_plan`: 0 occurrences across all 76 steps**, including the three tasks with
  `max_steps` 12-15. Same result as the D-a-time archive survey (0/19 runs). **This
  settles D-d's mechanism question**: the model will not spontaneously plan even on
  8-15-step tasks under this system prompt's "plan within your first 2-3 steps for
  non-trivial tasks" framing — apparently these still don't register as non-trivial
  enough, or the model just doesn't reach for the tool. D-d needs an **explicit planner
  call**, not native-schema harvesting.

## The one real finding: `copy_paste_notes`

The only non-"finished" outcome. `summary.json`: `success: false`,
`note: "max_steps reached"`, `steps_taken: 15` (the task's own budget). Human grade:
**pass**, `note: "passed on step 15"` — `auto_grade`/`auto_evidence` are both `null`
(no verdict was ever computed; the run never reached the `finished` action-kind that
triggers a shadow check).

The last three recorded actions (`step_12`–`step_14`) are: select-all (`ctrl+a`) in the
first document, type `notes.txt` into the save dialog's filename field, then click the
dialog's Save button. That is the task's actual terminal action. The model ran out of
budget **one step after correctly finishing the physical task** — never took the
following screenshot, never called `finished`, never got the chance to be checked by the
verifier at all. This is not confident-wrong-progress (there is no `False` grade to
compare against); it is the mirror case: **correct progress with no self-declared
checkpoint**, which is invisible to a terminal-only oracle by construction — the shadow
check never fires because the run never claims done.

This is a real gap a subgoal unit would close — it would have checked "document 2 saved
as notes.txt" as its own gated transition partway through, rather than needing the
*entire* task correct and self-recognized in one un-checkpointed 15-step run. **But it is
the wrong failure mode to satisfy D-d's stated gate.** It is *under-confident correct
progress* (did the work, ran out of budget before declaring it) — structurally the
mirror image of the *confident-wrong* progress D-d's plan gate names: "at least one
caught case of confident-wrong progress the flat loop missed." This battery produced
zero of those. The graded scoreline is a clean 10/10 sweep; the one non-"finished" run
is a near-miss on budget, not a caught mistake.

## Gate evaluation

- **D-c's hard gate** ("ships only if D-b measured false-refusal ≈ 0"): **met.**
  False-refusal 0/9 on live (not replayed) frames from fresh tasks is exactly what the
  gate names, and it clears. **D-c is a legitimate go.** (Its own plan text already
  hedges the open question this run leaves: false-confirmation is unmeasured, which is
  why D-c's design doesn't fully retire the human — every auto/human disagreement plus a
  random sample of agreements stays human-checked, precisely for cases like this one.)
- **D-d's gate** ("extended battery shows headroom, not another clean sweep"): **NOT
  met.** This battery is, at the graded level, another clean sweep — no case of
  confident-wrong progress occurred for D-d to demonstrably fix. `copy_paste_notes` is
  useful, real evidence (motivates subgoal-level checkpointing and/or budget tuning) but
  it doesn't satisfy the plan's own stated success criterion. D-d needs a battery — harder
  tasks, tighter budgets, or both — that actually produces the failure mode it exists to
  catch before its gate can be called clear.
- **D-d's mechanism question** (native plan harvest vs. explicit planner call): **settled
  regardless of the gate above — explicit planner call.** 0/76 spontaneous `update_plan`
  emissions here, 0/19 in the pre-existing archive; the native schema is not going to be
  populated by just asking nicely at a longer task length. This finding stands
  independent of whether/when D-d's gate clears.

## What's next

**D-c (flip the gates: in-loop terminal gating + battery auto-grading) is unblocked and
is the recommended next slice** — its hard gate is met on live evidence. **D-d is not
yet unblocked**: its mechanism is decided, but its own stated gate needs a battery run
that actually produces confident-wrong progress, which this one didn't. That's either a
harder task list, tighter step budgets to force more corner-cutting, or accumulated
evidence from D-c's own operation (its shadow-era agreement data won't be available once
gating is live, but disagreements D-c's spot-check surfaces are exactly this kind of
case) — worth deciding deliberately rather than defaulting to "just add more tasks."
