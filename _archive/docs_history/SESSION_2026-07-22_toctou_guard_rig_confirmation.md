# SESSION 2026-07-22 — TOCTOU pre-fire guard: rig-confirmed

## What this session was

The pre-fire target-tile guard (landed `docs/SESSION_2026-07-22_roadmap_
alignment.md`, offline-tested only at the time) had one open item since:
rig confirmation via an apples-to-apples GNOME battery rerun
(`docs/PLAN_2026-07-22_roadmap_alignment_slices.md` Part 4). The operator ran
two full batteries against `tools/battery_tasks_gnome.json` this session.

## Results

- `runs/battery_20260722_173742/results.json` — **5/5** (5 tasks, no
  `paint_line` in this run).
- `runs/battery_20260722_222137/results.json` — **5/5 (1 void)** (6 tasks,
  `paint_line` reinstated per its earlier setup note).

Both scores are at or above the 2026-07-21 baseline
(`runs/battery_20260721_235153/`, 4/4 (1 void)) — the guard did not cost
anything, and the extra task in the second run (`text_editor_type`, not in the
original baseline) also passed.

## Guard-refusal count

| Run folder | Steps | Guard refusals |
|---|---|---|
| `battery_text_editor_type_20260722_173847` | 6 | 1 |
| `battery_calc_multiply_20260722_174625` | 6 | 0 |
| `battery_settings_display_20260722_175145` | 2 | 0 |
| `battery_editor_save_file_20260722_175322` | 10 | 0 |
| `battery_top_bar_clock_20260722_175648` | 1 | 0 |
| `battery_text_editor_type_20260722_222235` | 3 | 0 |
| `battery_calc_multiply_20260722_222405` | 6 | 0 |
| `battery_settings_display_20260722_222610` | 2 | 0 |
| `battery_editor_save_file_20260722_222710` | 7 | 1 |
| `battery_paint_line_20260722_223124` | 20 | 2 |
| `battery_top_bar_clock_20260722_224703` | 1 | 0 |

4 refusals across 64 steps (~6%). Every one isolated (never 3 consecutive —
`GUARD_REFUSE_LIMIT` never triggered), and no task failed because of a
refusal. This reads as the guard doing its job at a reasonable rate, not
over-triggering on capture noise — no threshold recalibration indicated by
this data.

## Refusal walkthrough (the clearest case)

`runs/battery_editor_save_file_20260722_222710/step_04.json`: the model had
just clicked "Save As..." in the text editor's hamburger menu; its next
decision (still reasoning from that same decision frame) was to re-click the
hamburger menu at (887, 142) because "the menu closed but no save dialog
appeared." Between that decision and firing, the pre-fire grab found a region
tile diff of **70.5** at top-right (the menu already animating/changing) — the
guard refused, logged `guard_refusal: region tile diff 70.5 at top-right`,
`executed: false`, and the loop re-observed on the next step instead of
clicking into whatever had already moved. The task went on to pass. This is
exactly the paint_line-s09 class of race the guard was built to catch
(`docs/SESSION_2026-07-22_first_complete_battery.md`), now caught live on the
actual rig rather than only in the offline scripted tests.

The two `paint_line` refusals (steps 01 and 17) were both similarly legitimate
— a mid-launch icon/window state change and a UI element changing while
positioning the cursor before a drag. Neither caused the task's eventual void;
see below.

## paint_line: voided again, not a guard problem

`runs/battery_paint_line_20260722_223124/` — graded **void**, operator note:
"confusing app ui relying on nonstandard icons without labels, need to verify
its actions." This is a genuine Pinta-UI-legibility issue (icon-only toolbar,
no text labels for the model to ground against), independent of the guard —
both of this run's guard refusals were confirmed-legitimate catches (see
above), not the cause of the eventual void.

## Conclusion

**Roadmap §7 item 0 (the TOCTOU pre-fire guard) is now fully closed**: landed,
offline-tested, and rig-confirmed working correctly with a healthy (not
trigger-happy) refusal rate, no task regressions, and at least one clean
real-world catch of the exact race it was designed for.

## Follow-ups

- None specific to the guard. `paint_line`'s Pinta-UI-legibility issue is a
  separate, pre-existing task-design problem (not this session's to fix) —
  worth a note if the task list gets revisited.
