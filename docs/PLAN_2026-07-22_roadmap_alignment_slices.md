# PLAN 2026-07-22 — Roadmap alignment: corrections + slices (APPROVED)

_Approved 2026-07-22 in Claude Code plan mode; promoted to docs/ per AGENTS.md
§6 (any approved plan is committed at approval time). Status at promotion:
Parts 1, 2a-2c EXECUTED (commits 72b9aa6 hygiene, cce074f roadmap, bcc7f1d
TOCTOU guard; post-rerun addendum 5f77cc8). Part 3 (Slice B firmware Phase 0 —
scope since grown by the long-idle mouse-death diagnosis, PROJECT_STATE §4 —
and Slice C model seam) PENDING, each its own branch. Part 2b's
keep-the-filename note was superseded later the same day: the roadmap is now
`docs/ROADMAP.md`. Body below is the approved text, verbatim._

---

# Roadmap ↔ Code Alignment: findings, corrections, and first execution slice

## Context

`kvm agent roadmap.md` (untracked, repo root) is the new design compass for the
KVM agent. This session verified every factual claim in it against the live code
(firmware, transport, loop, model layer, battery, instrumentation) with file:line
evidence. Verdict: the roadmap's architecture-gap claims (§2 "Thin") are **all
accurate**, but a handful of assumptions are wrong or stale, and its §7 ordering
conflicts with the project's own measurement-gated principle. Per operator
decisions: fix the TOCTOU race first (before firmware Phase 0), execute
hygiene + that first slice this session, and annotate + git-track the roadmap.

## Part 1 — Roadmap inconsistencies / false assumptions (the flags)

These go into the roadmap file as minimal, clearly-marked corrections (Part 2c):

1. **"the battery has graders" (§2, verification bullet) — FALSE.** The battery is
   human-graded (`tools/battery.py:45-65`, p/f/v + note). No automated
   postcondition oracle exists anywhere; "automated fail-closed vision grading"
   is explicitly deferred in PROJECT_STATE.md §4.
2. **"you already log most of this" (§5) — HALF TRUE.** Logged/derivable:
   steps-to-completion, completion rate, per-step latency + tokens,
   honest-refusal vs budget-exhaustion (distinguishable via `answer_text`, not
   yet a computed rate). **NOT computed: grounding rate and verifier
   false-confirmation rate** — precisely the two metrics that gate Phases 4/5.
   (No verifier exists, so false-confirmation rate is currently unmeasurable.)
3. **Firmware gap 2 (UART lock-step) is half done already.** The host already does
   blocking send→await-PONG per command under a lock (`pikvm_proto._roundtrip`,
   176-193, timeout 1.0s). Only the **retry-on-failure** half is missing.
4. **Firmware gap 3 (SET_KBD/SET_MOUSE re-enumeration) is already mitigated.**
   The host defines no SET_KBD/SET_MOUSE commands at all (`pikvm_proto.py:111-116`);
   mode is set once at boot from GPIO defaults (abs USB mouse,
   `ph_outputs.c:93-99`) and persisted in watchdog scratch[0]. The recommended
   end state is the actual behavior; only firmware-side dead paths remain.
5. **The roadmap omits the two measured dominant live failures** (PROJECT_STATE §4,
   SESSION_2026-07-22): the decide-act TOCTOU race (screen re-flows during the
   model's ~15-20s think; a correct-at-decision-time click landed on the wrong
   row) and the semantically-misleading changed/unchanged tool-result binary.
   By the roadmap's own measurement-gating, these outrank §7's firmware-first
   ordering. They are the runtime slice of Phase 2's "verification stops being
   self-judged."
6. **Doc conflicts to reconcile:** PROJECT_STATE §4 lists "firmware HID watchdog"
   as *deferred* while roadmap §7 makes it immediate; CLAUDE.md header still says
   "physical Win10 target" (GNOME since 2026-07-21); `tools/battery.py:91-93`
   still **defaults to the Windows task file** (`battery_tasks_shakedown.json`) —
   a trap for the pending "apples-to-apples GNOME rerun."
7. Confirmed-accurate (no change needed): all §2 "Solid" claims
   (abs-pointer 0-32767, LED readback in every PONG, per-command confirmation,
   CRC UART, ACK'd HTTP, flushed JSONL wire log, phantom-scroll fix at
   `ph_usb.c:196-201`); all seven §2 architecture-gap claims verified at
   file:line in `agent_loop_holo.py`/`holo.py`; watchdog absent
   (no `watchdog_enable`/`watchdog_update` in `pico_fw/src`); no timed-motion
   primitive (host teleport-drag, `appliance.py:96-100`); multi-monitor
   unaddressed (moot: single lid-closed laptop); hardware = B70 only, A770 not
   in the live path (Phase-5 question stands, operator decision).

## Part 2 — Execute this session (on this branch, three separate commits)

### 2a. Hygiene commit (~10 lines)
- `tools/battery.py` main(): **require an explicit task-file argument** (exit
  with usage text when absent) instead of defaulting to the Windows shakedown
  list — fail-closed, consistent with repo ethos.
- `CLAUDE.md` repo-layout line: "(Holo3.1, physical Win10 target)" →
  "(Holo3.1, physical target — Ubuntu/GNOME as of 2026-07-21; see PROJECT_STATE.md)".
- `PROJECT_STATE.md` §4: move "firmware HID watchdog" from Deferred to a
  "scheduled: Phase 0 slice" note (kills the docs conflict).
- Update `tests/test_battery.py` if it exercises the default-file path.

### 2b. Roadmap commit
- Apply the Part-1 corrections to `kvm agent roadmap.md` as minimal inline
  annotations (marked `[correction 2026-07-22: …]`, preserving the author's
  voice/intent), add TOCTOU + tool-result-signal to §2 gaps and to §7 ordering
  (as step 0), then `git add` the file (rename to `docs/ROADMAP.md`? No — keep
  the user's filename and location; just track it).

### 2c. Slice A commit — TOCTOU pre-fire target-tile guard + tool-result magnitude upgrade
Branch stays `kvm-working-branch`; files: `agent_loop_holo.py`,
`kvm_agent/hardware/env.py`, `tests/test_agent_loop.py`, `tests/test_frame_diff.py`.

**Guard design** (per Plan review; the project's own sketch in
SESSION_2026-07-22 finding 2, refined):
- **Metric home:** new `tile_region_max_png(png_a, png_b, x, y, screen_w,
  screen_h, radius=1)` in `kvm_agent/hardware/env.py` (the mandated single home
  of the tile metric): max tile-mean diff over the 3×3 tile neighborhood
  (clamped at edges) centered on the tile containing (x, y); 9×16 grid.
- **Wiring:** in the batch loop (`agent_loop_holo.py:462-515`), for the **first
  screen-affecting coordinate action of each batch** (kinds `left_click`,
  `double_click`, `drag_to` using the drop coordinate), **reuse the existing
  `before = _frame_png()` grab at line 469** as the fresh pre-fire frame:
  `score = tile_region_max_png(png, before, x, y, w, h)` where `png` is the
  decision frame (same buffer read as the model-input JPEG). If
  `score > FRAME_CHANGE_THRESHOLD`: do NOT call `_execute`; emit tool_output
  `NOT executed: the screen changed near the target (region tile diff N at
  <region>) between your decision and firing — the click was not performed and
  the remaining calls in this step were not executed. Re-examine the next
  screenshot before retrying.` and halt the batch (native halt-on-error
  semantics). Later actions in a batch are unguarded by design: the model
  decided them anticipating its own earlier effects, so no reference frame
  exists (document in a comment).
- **Anti-contamination preserved:** refuse-to-advance only; no injected retry,
  no suggested coordinates. Track `screen_touched` so `[update_plan, click]`
  still guards the click.
- **Livelock stop:** consecutive `guard_refusals` counter; guard refusals do NOT
  count against `STUCK_LIMIT`, but 3 consecutive → abort loudly
  (`recorder.finish(False, note="target region unstable across 3 decision
  cycles")`). Recorder: `executed=False`-style step warning `guard_refusal`.
- **Magnitude/region upgrade:** `_frame_diff_detail` additionally returns
  changed-tile count; result strings become e.g. `Executed. Screen changed:
  widespread (41/144 tiles, strongest top-left, max tile diff 27.3).` /
  `localized (2/144 tiles, center, …)` — additive text in the existing
  `<tool_output>` channel, no schema/prompt change. Keep a compat wrapper for
  any test importing `_frame_diff_score`.

**New tests (offline):**
- `tests/test_frame_diff.py`: region metric — in-region change detected,
  equal-magnitude out-of-region change NOT detected, corner clamping (0,0 and
  w-1,h-1).
- `tests/test_agent_loop.py`: scripted camera (frame queue) scenarios:
  (1) changed target region → no click in FakeR4 calls, "NOT executed" +
  "changed near the target" in tool_output, step threaded, run continues;
  (2) clean pre-fire → fires; only first batch action guarded;
  (3) change away from target → fires (no false refusal);
  (4) 3 consecutive refusals → loud abort with unstable-target note;
  (5) `[update_plan, left_click]` still guards the click.

## Part 3 — Planned next slices (NOT executed this session; summarized for the record)

- **Slice B — Phase 0 firmware hardening** (own branch, needs rig time):
  (1) HW watchdog in `main.c`: `watchdog_enable(1000, true)` after init; pet
  gated on `!_reset_required` (preserves the deliberate 100ms mode-change
  `watchdog_reboot`); read `watchdog_enable_caused_reboot()` BEFORE enable;
  surface it as new PONG flag `0x20` (verified free; legacy RESP_OK, and error
  codes lack the 0x80 PONG bit so no ambiguity) OR'd into every successful
  response; host `decode_code` + bridge `/health` + wire log surface it loudly.
  Verify pico-sdk watchdog code doesn't touch scratch[0] (mode persistence)
  before landing. (2) Host retry in `_roundtrip`: NACK responses (0x40/0x45/0x48)
  → retry any command; ambiguous failures (no/garbled response) → retry only
  idempotent commands {PING, CLEAR_HID, KBD_KEY, MOUSE_ABS, MOUSE_BUTTON — all
  absolute state assertions}, never MOUSE_WHEEL (relative); 2 retries, 150ms
  pre-retry pause (> the firmware's 100ms UART resync idle gap — retry doubles
  as resync); `retries` count in every response + wire log. (3) New
  `tools/soak.py` for the Phase-0 gate: probe every 10s, benign corner
  mouse-move + camera-liveness check every 5min, JSONL to `runs/soak_<ts>/`,
  operator-driven fault injection (UART unplug, bridge restart); gate =
  overnight ≥8h, every failure line maps to an injected fault. New offline
  `tests/test_pikvm_proto_retry.py` (fake serial). Deploy: rebuild `hid.uf2`
  (make), BOOTSEL flash, scp `pikvm_proto.py`/`hid_bridge.py` to the Pi 5,
  restart hid-bridge, `/health` check, camera-verified HID gate, then soak.
  Deferred within Phase 0: timed-motion drag (until a task needs it),
  multi-monitor (out of scope), SET_KBD/SET_MOUSE removal (optional hygiene).
- **Slice C — Phase 1 model seam** (own branch, pure refactor): one
  `ModelSession` Protocol (`kvm_agent/models/base.py`) with `decide(frame_jpeg,
  w, h, instruction) -> StepDecision` + `commit(decision, results)` — NOT three
  propose/ground/verify methods yet (Holo fuses propose+ground; verify doesn't
  exist as a call; the roadmap gate is about loop vocabulary, and `verify()`
  joins the Protocol in Phase 2). Move into `HoloSession`: history ownership,
  observation/tool_output construction, image trim, data-URL encoding, the
  inline tool-name map (`agent_loop_holo.py:464-468`). Prove "pure refactor"
  with a **golden-transcript test**: generate a fixture of per-step request
  payloads from the CURRENT code path first, then assert byte-identical
  payloads through `HoloSession`; watch the double-trim (loop:533 +
  `call_holo_full`:605) equivalence. Plus battery rerun, score unchanged.

## Part 4 — Readiness tests (run before/after this session's changes)

1. **Offline gate (before any rig time, AGENTS.md §4):** `python -m pytest tests/`
   — currently 57 tests; must be green pre-change, ~63 green post-Slice-A.
2. **Rig checklist (operator):** `tools/hid_smoke.py` → `boot()` camera-verified
   HID gate → **the pending apples-to-apples GNOME battery rerun**
   (`python tools/battery.py tools/battery_tasks_gnome.json`) — this is both the
   readiness assessment the roadmap's measurement philosophy demands AND the
   before/after diff for Slice A. Gate: score not worse than 4/4 (1 void)
   baseline; grep run logs for guard-refusal rate (high refusals on static
   screens = threshold too hot → calibrate).
3. **Phase-0 gate (after Slice B, future):** overnight soak, zero unexplained
   gaps.
4. Roadmap metrics still unmeasured (grounding rate, verifier
   false-confirmation) become measurable only after Phase 2's verifier exists —
   noted in the roadmap annotation so §5 stops overpromising.

## Verification for this session

- `pytest tests/` green (all new + existing).
- `python tools/battery.py` with no args exits with usage (fail-closed).
- Guard behavior demonstrated entirely offline via the scripted-camera tests;
  rig confirmation rides the operator's next battery rerun.
- `git status` clean at session end (AGENTS.md: commit-or-revert), three
  reviewable commits: hygiene, roadmap-annotate+track, TOCTOU guard.
