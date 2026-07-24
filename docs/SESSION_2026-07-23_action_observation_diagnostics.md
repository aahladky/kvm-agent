# Session 2026-07-23 — action/observation diagnostics

## Outcome

Recorded loop steps now preserve the timing and frame-sequence evidence needed to
distinguish capture staleness, premature settling, decide–act TOCTOU changes, and guard
noise. This is observability only: action execution, settle thresholds/windows, guard
decisions, model-facing tool output, and abort behavior are unchanged.

## Per-action evidence

Each `step_NN.json` has an `action_diagnostics` list aligned to attempted actions. A
normal executed action records:

- exact decision, pre-fire, first-post-HID, and post-action capture sequences;
- the exact guarded pre-fire and executed post-action PNGs already captured by the
  loop, referenced by filename from the diagnostic record;
- HID return and freshness-wait timestamps/durations;
- settle start/end/elapsed time, status, required/observed stable frames, fresh-frame
  count and sequence range, peak tile diff, and first above-threshold change time;
- the post-action frame-diff magnitude, region, spread, and changed/unchanged result.

A guarded action additionally records the target-region diff, refusal, whether it
repeats the previous executed action, and the previous action's step/index. A refusal
of a repeated prior action is labeled `late_effect_candidate`. The name is deliberately
heuristic: an unrelated asynchronous change can produce the same shape, so the label
selects cases for frame review rather than asserting root cause.

Production camera reads pair frames and sequences atomically. Compatibility fallbacks
remain for offline adapters that expose only the earlier read/observe surface.

## Battery diagnosis

`tools/battery_metrics.py` remains backward compatible with earlier step files and now
reports:

- diagnostic coverage and action-attempt count;
- repeated action attempts;
- steps lost to guard refusals;
- repeated-action guard refusals and late-effect candidates;
- settle status counts, latency and fresh-frame distributions, and the number of
  settles where a significant change began inside the settle window.

Legacy runs retain their warning-derived guard-refusal rate. Timing-dependent metrics
show zero coverage instead of treating absent fields as observed zeros.

## Verification

- Focused settle, loop, recorder, metrics, and frame-buffer tests: 66/66 pass in
  `runs/offline_tests_20260723_223814/pytest.txt`.
- Complete cache-free deterministic suite: 195/195 pass in
  `runs/offline_tests_20260723_223845/pytest.txt`.
- Live model/request/parser contract: 4/4 pass in
  `runs/model_contract_smoke_20260723_224008/summary.json`.
- Bounded physical capture→model→HID calibration: pass in five steps in
  `runs/physical_calibration_smoke_20260723_224111/summary.json`.

The physical actor record at
`runs/physical_calibration_smoke_20260723_224111/physical_calibration_actor_20260723_224121/step_00.json`
confirms the schema with real capture data. Its first click records decision sequence
277, pre-fire sequence 744, first post-HID sequence 746, post-action sequence 773,
retained pre/post PNGs, a render onset 0.101 seconds into settling, and stable completion
after 19 fresh frames. No general application battery was run. The next battery can
compare guard cost against the prior 8/76 refusal baseline.
