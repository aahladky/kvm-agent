# Session 2026-07-23 — post-action settle window

## Outcome

The live loop now requires 15 consecutive fresh low-change frames before accepting a
post-action screen as settled. At the existing 50 ms polling interval this is about
0.75 seconds of continuous visual quiet, still bounded by the existing 1.5-second
settle timeout.

This is a narrow mitigation for the 2026-07-23 battery pattern in which three quiet
frames arrived before an asynchronous app launch, dialog, or theme render began. The
model could receive that premature state, repeat its prior action, and spend a step on
a pre-fire guard refusal. The guard remains unchanged.

## Changes

- `agent_loop_holo.py` passes `stable_frames=15` to the existing seq-aware
  `wait_until_stable()` call.
- `tests/test_settle.py` reproduces a delayed render after ten initially quiet frames
  and proves settling waits for the render plus a complete new quiet window.
- The short capture-stall fixture now expects `timeout`: its deliberate 0.3-second
  window cannot honestly satisfy the production 15-frame quiet requirement.
- `PROJECT_STATE.md` records the active settle contract and current test count.

No physical battery or rig action was run. The next physical task should compare
guard-refusal frequency with the prior 8/76 baseline; a lower rate is expected, but is
not claimed by the offline test.

## Evidence

- Focused settle and loop tests: 43/43 pass in
  `runs/settle_regression_20260723_221327/focused_pytest.txt`.
- Complete cache-free deterministic suite: 193/193 pass in
  `runs/offline_tests_20260723_221518/pytest.txt`.
- The first full-suite attempt correctly rejected `__pycache__` created by an earlier
  non-cache-free focused invocation:
  `runs/offline_tests_20260723_221418/pytest.txt`. Those generated directories were
  removed before the passing run.
