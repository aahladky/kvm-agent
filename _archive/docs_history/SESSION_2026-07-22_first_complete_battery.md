# SESSION 2026-07-22 — First complete battery: review, root-causes, void grade

## The run

`runs/battery_20260721_235153/results.json` — 5 tasks, started 2026-07-21 23:51,
finished 00:10. GNOME target, native 720p capture (`SCREEN_W/H=1280x720`),
`HOLO_HISTORY_IMAGES=3`, psr moot (Ubuntu). First battery ever graded to completion.

**Recorded 5/5. Honest score: 4/4 (1 void).**

| task | steps | verdict |
|---|---|---|
| notepad_type | 6 | pass — model adapted "Notepad" → GNOME Text Editor unprompted |
| calc_multiply | 6 | pass — clean 5-click sequence; **0/20 the previous morning** (OS switched Windows→Ubuntu same day, so not a controlled A/B) |
| settings_display | 7 | pass — recovered from wrong landing page |
| paint_line | 20 (max) | **infeasible, not failed** — no paint app exists on the target; model searched Paint/Pinta/Krita, then went to App Center to install one |
| taskbar_clock | 1 | pass — answer "Jul 22 00:10" matches the run timestamp exactly (free ground-truth) |

## Finding 1 — the grade vocabulary failed open (finding #8's class)

`grade_task` accepted only p/f, so the operator force-graded infeasible paint_line
"pass" with the protest note "**neither p nor f…", inflating the score to 5/5 —
an uncertain grade masquerading as a pass, in the tool built to prevent exactly that.

**Fixed this session:** `v <required note>` = void; excluded from the score's
denominator, surfaced as `"4/4 (1 void)"`; a bare `v` re-asks. The recorded
results.json is NOT retro-edited (evidence is immutable) — its honest reading is
4/4 (1 void). Tests: `tests/test_battery.py` (void-note-required, denominator).

## Finding 2 — the paint_line "misclick cascade" was a decide-act TOCTOU race

Steps 9–11 looked like the model clicking garbage. Frame-by-eye walk
(`runs/battery_paint_line_20260721_235845/`) says otherwise:

- Coordinates are 0–1000 normalized, projected `raw/1000 × screen` (holo.py,
  calibrated ≤17.5px). Step 10's click (785,200) → [1005,144] landed EXACTLY on the
  Text Editor close button the model named, and worked — the projection basis is right.
- Step 9's click (350,195) → [448,140] lands squarely on the "pinta — App Center"
  search row **in the decision frame** (`step_09.png`). But the outcome was a FRESH
  Firefox launch (Privacy Notice first-run tab, `step_11.png`) googling "PintaKrita" —
  i.e. the "Search online" row, 90px BELOW the click point, was activated.
- Only consistent explanation: during the model's **18.8s think time**, GNOME's async
  search re-flowed — the slow App Center snap provider's row dropped out and the
  Firefox row slid up under the already-chosen coordinates. Corroboration: when App
  Center finally opened (step 17, via dock), it was on its HOME page — the step-9
  provider activation never fired.

The freshness floor guarantees a fresh frame BEFORE the model call; nothing re-checks
the screen AFTER model latency. Two milder instances of the same disease in this
battery: settings s00–s01 double-clicked the dock because the app launched slower
than a step; notepad s02 acted on a "Searching…" spinner frame.

**Theme: target-side async latency (GNOME search providers, snap cold starts) vs the
~15s step cadence is now the dominant failure source.**

**Proposed fix (fold into the signal-redesign session):** pre-fire target-tile guard —
re-grab a frame immediately before firing each click, tile-diff the region around the
target coordinate against the decision frame (metric already single-homed in
`kvm_agent.hardware.env`); unchanged → fire, changed → do NOT fire, report
"screen changed since decision, click NOT executed" in `<tool_output>` (the
NOT-executed vocabulary already exists) and re-observe.

## Finding 3 — correction to the operator's run note

The model was NOT "unable to select or delete text": it never attempted select/delete.
It cleared the GNOME search box via the X-button (steps 3, 13) and that worked. The
visible cost was GNOME Activities appending each `type` to the existing query
("PaintgnomePaint", "PintaKrita") — a target quirk, recovered from correctly.

## Changes this session

- `tools/battery.py`: void grade (`v` + mandatory note); score = passes over
  non-void tasks, voids surfaced in the string. `tests/test_battery.py` extended
  (suite 57 green).
- `tools/battery_tasks_gnome.json`: GNOME-honest task list — paint_line replaced by
  `editor_save_file` (save-dialog flow), Notepad/taskbar phrasing neutralized.
  `battery_tasks_shakedown.json` untouched (it's the Windows list).
- Committed the pending native-720p default (`kvm_agent/config.py` — matches what
  this battery actually ran, per meta.json `screen_size [1280,720]`).
- `AGENTS.md` §5: blame-ledger row for the paint_line cascade. `PROJECT_STATE.md`
  updated (baseline complete; TOCTOU + slow-launch open problems).

## Follow-ups

- Pre-fire target-tile guard (above) — with the magnitude/region tool-result redesign.
- ~~`HOLO_HISTORY_IMAGES` A/B~~ — RESOLVED 2026-07-22: operator keeps 3 (the
  default, native's max_images) as standing config; A/B dropped after the 4/4 battery.
- ~~Confirm Pico reflash + Pi 5 deploy~~ — RESOLVED 2026-07-22: operator confirms
  both were current BEFORE this battery, so the 4/4 ran on the fixed firmware/proto.
- Run the GNOME battery (`tools/battery_tasks_gnome.json`) for the first
  apples-to-apples rerun.
