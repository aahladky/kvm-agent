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

- **Completion: 10/10 human-graded pass, 0 voids.** Not another ceiling sweep, though —
  see below.
- **False-"finished" rate: 0/9 (0.0%).** Every run that claimed `finished=True` was
  graded a true pass. Phase 2's headline failure mode did not occur on this battery.
- **Verifier agreement: 9/9 comparable, 100%, false-refusal 0/9 (0.0%), false-confirmation
  0/9** (rate reported `null`, not `0.0` — there were zero true-fail cases in this batch
  to divide by, `battery_metrics.py` correctly declines to manufacture a rate from an
  empty denominator). **This is the number that gates D-c, and it clears the gate.**
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

This is exactly the shape of gap D-d exists to close: a subgoal unit would have checked
"document 2 saved as notes.txt" as its own gated transition partway through, rather than
needing the *entire* task correct and self-recognized in one un-checkpointed 15-step
run. It's also a legitimate quantity-of-headroom data point for the "not another ceiling
sweep" plan gate — 9/10 clean, 1/10 real budget-boundary failure on an genuinely harder
task, which is the kind of result D-d has something to fix.

## Gate evaluation

- **D-c's hard gate** ("ships only if D-b measured false-refusal ≈ 0"): **met** — 0/9,
  0.0%, on live (not replayed) frames from fresh tasks, agreeing with human grades 9/9.
- **D-d's gate** ("extended battery shows headroom, not another clean sweep"): **met** —
  `copy_paste_notes`'s budget-exhaustion-despite-correct-completion is a real,
  non-hypothetical case a subgoal unit would have caught earlier than the terminal
  claim.
- **D-d's mechanism question** (native plan harvest vs. explicit planner call):
  **settled — explicit planner call.** 0/76 spontaneous `update_plan` emissions here,
  0/19 in the pre-existing archive; the native schema is not going to be populated by
  just asking nicely at a longer task length.

## What's next

D-c (flip the gates: in-loop terminal gating + battery auto-grading) and D-d (the
subgoal unit) are both unblocked by these numbers, per
`docs/PLAN_2026-07-22_phase2_subgoal_verification.md`'s own ordering. D-c is the smaller,
lower-risk slice and is next in the plan's sequence.
