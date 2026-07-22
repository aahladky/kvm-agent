# SESSION 2026-07-22 — Roadmap↔code alignment review + pre-fire TOCTOU guard

## What this session was

`kvm agent roadmap.md` (the design-session synthesis) was checked claim-by-claim
against the live tree, the misalignments were fixed or annotated, and the first
measurement-gated work item landed: the pre-fire target-tile guard from
SESSION_2026-07-22_first_complete_battery finding 2, plus the tool-result
magnitude/spread upgrade. Three commits: hygiene, roadmap annotate+track, guard.

## Alignment verdict (full flags annotated inline in the roadmap file, now tracked)

Accurate: all seven §2 architecture-gap claims (flat loop, unbounded in-context
notes, decorative update_plan, no subgoals, change-detection-only verification,
abort-only recovery, single-shot grounding) and every §2 "solid" firmware/
transport claim, verified at file:line. Wrong or stale:

- "the battery has graders" — it is human-graded (p/f/v); no automated oracle
  exists anywhere in the live tree.
- "you already log most of this" (§5) — grounding rate and verifier
  false-confirmation rate, the two Phase-4/5 gate metrics, are NOT computed.
- UART lock-step (§2 gap) is half done: the host already blocks send→await-PONG
  per command; only retry-on-failure is missing.
- SET_KBD/SET_MOUSE re-enumeration (§2 gap) is already mitigated: the host never
  issues them; mode is set once at boot from GPIO defaults.
- The roadmap omitted the two measured dominant failures (TOCTOU race,
  misleading changed/unchanged signal); added as §2 gaps and §7 step 0 per its
  own measurement-gating rule. Operator decision this session: TOCTOU guard
  before Phase 0 firmware hardening.

## Changes

- **Hygiene** (`72b9aa6`): `tools/battery.py` requires an explicit task file
  (the silent default was the WINDOWS list); CLAUDE.md "Win10 target" line
  corrected; PROJECT_STATE's deferred-list/watchdog conflict resolved.
- **Roadmap** (`cce074f`): tracked in git + inline `[correction 2026-07-22]`
  annotations for everything above.
- **Pre-fire TOCTOU guard** (`agent_loop_holo.py` + `kvm_agent/hardware/env.py`):
  - New `tile_region_max_png(png_a, png_b, x, y, w, h, radius=1)` in env.py (the
    tile metric's single home): max tile diff over the 3×3 neighborhood around
    the target pixel; returns (score, row, col).
  - In `run()`'s batch loop: the FIRST screen-affecting coordinate action
    (`GUARD_KINDS` = left_click / double_click / drag_to-drop) compares the
    decision frame (`png`, the exact frame the model saw — single buffer read)
    against the existing per-action `before` grab (zero added capture cost). If
    the region diff exceeds `CFG.frame_change_threshold`: the action is NOT
    fired, the batch halts, the model gets "NOT executed: the screen changed
    near the target … re-examine the next screenshot", and the recorder logs
    `executed=False` + a `guard_refusal` warning. Refuse-to-advance only — no
    injected retries (the 2026-07-19 anti-contamination rule holds).
  - Batch actions 2..N stay unguarded by design: the model decided them
    anticipating its own earlier actions' effects, so no reference frame exists.
  - Guard refusals do NOT count against STUCK_LIMIT (the model wasn't wrong);
    `GUARD_REFUSE_LIMIT=3` consecutive refusals abort loudly ("target region
    unstable across 3 decision cycles") — the spinner/animation livelock stop.
  - Tool-result upgrade: `_frame_diff_detail` now also counts threshold-crossing
    tiles; executed actions report "Screen changed: localized (2/144 tiles,
    strongest center, max tile diff 6.1)" vs "widespread (41/144 …)" — additive
    text in the existing `<tool_output>` channel, no schema/prompt change.
    `tools/probe_resolution_ab.py`'s synthetic history sample updated to match.
- Tests: 57 → **66 green** (offline). New: region metric in/out/clamp
  (`test_frame_diff.py`); guard refuse-and-continue, first-action-only,
  off-target change ignored, livelock abort, update_plan-doesn't-burn-the-guard
  (`test_agent_loop.py`, scripted-observe fake env).

## Follow-ups

- **Rig confirmation**: the apples-to-apples GNOME battery rerun
  (`python tools/battery.py tools/battery_tasks_gnome.json`) — gate: score not
  worse than 4/4 (1 void), AND grep the run logs for `guard_refusal` rate: high
  refusals on static screens = the 3×3 region is too hot for the analog noise
  floor (calibration risk; threshold is the already-calibrated
  `CFG.frame_change_threshold`).
- Next slices per the plan (each its own branch): Phase 0 firmware hardening
  (1s HW watchdog + boot-reason PONG flag 0x20, idempotency-aware UART retry
  with the 150ms resync pause, `tools/soak.py` overnight gate), then the
  Phase 1 model seam (`ModelSession.decide/commit`, golden-transcript fixture).

## Post-rerun addendum (operator notes, 2026-07-22)

The battery rerun "largely completed as expected". Two follow-ups recorded:

1. **Long-idle mouse death → manual Pico replug.** Diagnosed same day (details
   in PROJECT_STATE §4): the firmware's mouse suspend path drops events while
   PONGing OK (`ph_usb.c:235`, kbd path retains and re-sends); remote wakeup is
   advertised but its host-side enablement is unverified. Folded into the
   Phase 0 firmware slice: retain-and-resend for mouse, suspend bit in the
   PONG, bridge keep-alive as fallback; the soak harness's long-idle window is
   the test bed.
2. **paint_line reinstated** in `tools/battery_tasks_gnome.json` with a setup
   note requiring a preinstalled paint app (Drawing/Pinta) — the Win10-era task
   returns now that its 2026-07-21 void cause (no app) is addressable by setup.
