# SESSION 2026-07-23 — Phase 2 slice D-b: shadow wiring, harder tasks, metrics

## What this session was

Slice D-b of `docs/PLAN_2026-07-22_phase2_subgoal_verification.md`: wire the D-a oracle
into `run()` in **shadow mode** (records a verdict, changes nothing about control flow),
extend the battery with longer end-state tasks so Phase 2 has headroom to measure, and
build the metrics aggregator roadmap §5 has always named and never had.

**Everything in this session is offline-complete and validated.** The one thing this
session does NOT do — cannot do, from here — is the actual rig session: running the
extended battery on the physical target with `verify_mode="shadow"` live. That is the
next, and only remaining, step. Everything below is what makes that one rig session
sufficient to answer all of D-b's open questions at once.

## The design

`run(verifier=None, verify_mode="off")`, `verify_mode ∈ {"off", "shadow", "gate"}`.

- **`"off"` (default) is provably identical to pre-D-b `run()`** — not just
  behaviorally, but in the exact shape of the return dict. All six of `run()`'s exit
  points now go through one `_result(finished, answer_text)` closure so they can't drift
  out of sync on this; in `"off"` mode it returns exactly `{"finished", "answer_text"}`,
  no third key, ever. The four pre-existing tests that assert exact dict equality on
  `run()`'s return needed zero changes.
- **`"shadow"`**: on a `finished` action, the verifier judges the exact same `after`
  frame the batch loop already captured for that action's own `tool_output` (no extra
  capture) — encoded via `png_to_model_input_jpeg` (new, single home in
  `kvm_agent/hardware/env.py`, alongside `model_input_jpeg`), the same client-side
  encoding path the actor's own model input takes, so a live verdict is comparable to
  `tools/verify_replay.py`'s offline D-a numbers rather than judging a
  differently-processed image. The verdict is recorded (per-step in the recorder, and
  in the run's own return as `verified_finish`) but changes nothing about whether/how
  the run concludes.
- **`"gate"` is rejected loudly** (`NotImplementedError`), not silently treated as
  `"shadow"` — that's slice D-c, not built yet, and a quiet no-op here would hide that
  the gate everyone assumes is active isn't.
- **Defense in depth on the verifier call itself**: wrapped in `try/except`, same
  reasoning as the existing `session.decide()` guard (P0-2) — a conforming `Verifier`
  never raises for a model-side failure (its own Protocol contract), but an unexpected
  raise from any verifier must not propagate past `recorder.finish()` and kill every
  remaining battery task. Absorbed into `Verdict(satisfied=None, evidence=f"verifier
  call raised: {e}")`, not trusted to have honored its contract.
- **Eager validation**: an unknown `verify_mode`, `"gate"`, or `"shadow"` with no
  verifier are all rejected before a single step runs — not discovered only when a run
  happens to never finish (stuck limit, max_steps), which would otherwise hide a
  misconfigured battery for its entire duration.

## Changes

- **`kvm_agent/hardware/env.py`**: `png_to_model_input_jpeg(png_bytes, target_h)` — the
  single home for "decode an evidence PNG back into the client's model-input JPEG",
  reused by both the live path and (refactored in the same change) `tools/
  verify_replay.py`'s `frame_data_url`, so the two can't drift apart on encoding.
- **`kvm_agent/models/base.py`**: `Verdict.to_dict()` — the compact JSON-safe record
  (`satisfied`, `evidence`, `wall_time_s`, `usage`) kept alongside a run; deliberately
  excludes `raw` (already captured in `kvm_agent.models.holo.REQUEST_LOG`, tagged
  `kind="verify"` since D-a — duplicating it here would be a second, driftable copy).
- **`agent_loop_holo.py`**: the wiring above. `VERIFY_MODES` constant; module docstring
  gains a changelog entry alongside the existing native-verbatim/TOCTOU-guard entries.
- **`kvm_agent/instrumentation/run_log.py`**: `log_step(..., verification=None)` — a
  per-step verdict dict, `None` on the overwhelming majority of steps (only a `finished`
  step's batch ever carries one). `finish()` gains `verifications` (parallel to
  `actions`, one entry per step) and `verified_finish` (the run's own terminal verdict,
  pulled via a reverse search so a run with no verification anywhere still reports
  `None` cleanly).
- **`tools/battery.py`**: `python tools/battery.py <tasks.json> [verify_mode]`
  (`off`|`shadow`, default `off` — unchanged CLI behavior when omitted). `HoloVerifier()`
  constructed once per battery (stateless, so nothing about reusing it across tasks is
  unsafe). `auto_grade_from_verdict()` maps the oracle's verdict into the same pass/fail
  vocabulary `grade_task` uses, added to each results row as `auto_grade` +
  `auto_evidence` **alongside** the human grade — `grader` stays `"human"`, nothing is
  replaced. Fail-closed the same way `grade_task` is: `satisfied=None` maps to `(None,
  evidence)`, never a silent pass.
- **`tools/battery_tasks_gnome.json`**: four new tasks (`file_create_rename`,
  `dark_mode_confirm`, `clock_to_file`, `copy_paste_notes`), ~8-15 steps each, ≥3 natural
  subgoals, and **every one phrased as an end state**, not a bare action — the lesson
  from D-a's one real miss (`small_target_tray`, an action-phrased task the oracle
  false-confirmed by checking the target existed rather than the action's effect).
  `clock_to_file` deliberately reuses the "free ground truth" trick from `top_bar_clock`
  (the wall-clock time at task completion is independently knowable) but forces the
  model to carry that observation across steps into a save — a small, deliberate probe
  of the in-context-memory gap roadmap §2 names.
- **`tools/battery_metrics.py`** (new): aggregates `results.json` + each task's
  `summary.json`/`step_NN.json` into every metric roadmap §5 names except grounding
  rate (a different eval shape, out of scope here): completion rate, steps-to-completion
  (finished vs aborted, separately), **false-"finished" rate** (finished=True but graded
  fail — confident-wrong progress, Phase 2's headline number), verifier
  agreement/false-refusal/false-confirmation (only where a battery ran `shadow`),
  guard-refusal rate (scans every step's `warnings`, previously only ever hand-counted
  in session docs), actor vs. verify latency as separate distributions plus their
  combined cost (holo3.1 serves with `--parallel 1`, confirmed in the serving-contract
  session, so a verify call serializes behind the actor call rather than overlapping
  it), and an honest-refusal-vs-budget-exhaustion split (the latter from the recorder's
  own unambiguous abort `note`; the former a labeled *heuristic* on the answer text,
  never presented as a certainty). Reuses `tools/verify_replay.py`'s own
  `task_run_dirs`/`resolve_run_dir` join logic rather than re-implementing it.

## Verification

- `python -m pytest tests/` — **157 passed** (was 131). New: 9 in `test_agent_loop.py`
  (off is byte-identical / ignores a supplied verifier / rejects unknown modes / rejects
  `gate` / requires a verifier for `shadow` / records a verdict without changing control
  flow / records an unsatisfied verdict / absorbs a raising verifier / `verified_finish`
  present-and-None on every abort path), 3 in `test_run_log.py`, 4 in `test_battery.py`,
  10 in `tools/battery_metrics.py`'s new test file.
- **Golden-transcript equivalence still holds** (`tests/test_model_seam.py`, 7 tests) —
  proves `verify_mode="off"`'s default path is untouched at the byte level, not just at
  the return-dict level.
- **`battery_metrics.py` cross-validated against known ground truth**: run against the
  real archive, its guard-refusal count (4/104 steps across the three graded batteries,
  3/39 on the latest alone) matches `docs/SESSION_2026-07-22_toctou_guard_rig_
  confirmation.md`'s hand-counted "4 refusals across 64 steps" exactly once the one
  pre-guard battery (`battery_20260721_235153`, which predates the guard's existence) is
  correctly excluded from that 64-step comparison.
- **Live smoke of the actual live-wiring encode path** (not just mocks): a real archived
  frame (`runs/battery_editor_save_file_20260722_222710/step_06.png`) pushed through
  `png_to_model_input_jpeg` → `HoloVerifier().check(...)` returned `satisfied=True` with
  legible evidence, in 5.2s — matching what D-a's offline replay already scored for that
  exact frame/task pair, confirming the live path and the offline eval judge identically
  encoded images.
- A found-and-fixed gap in `battery_metrics.py --all`: several pre-2026-07-21
  `battery_<ts>/` directories in this repo's own archive predate `results.json`
  (`battery_summary.json` was the old layout) and were silently contributing zero rows
  under a misleadingly "analyzed" label. Fixed: `analyze_battery` now returns `None` and
  prints an explicit `SKIPPING` line for any battery dir with no `results.json`, and
  `main()` filters those out before aggregating.

## What's left

**One rig session, per the plan's own framing** ("one rig session buys everything"):

```
python tools/battery.py tools/battery_tasks_gnome.json shadow
python tools/battery_metrics.py
```

That single run produces, simultaneously:
- the flat-loop baseline on the four new harder tasks (D-2's real before-picture),
- the live false-refusal rate (gates slice D-c — D-a's offline number was 0.0 on
  replayed frames; this is the same thing on fresh ones),
- verifier-vs-human grading agreement (`auto_grade` vs `grade` in the results row),
- and, for free, whether the model spontaneously emits `update_plan` on the new
  8-15-step tasks (decides D-d's mechanism: harvest the native plan schema, or build a
  bespoke planner call).

**Gates before D-c can flip anything**: the extended battery must show *headroom* (not
another clean sweep — otherwise D-d has nothing to prove), and shadow verdicts must
agree with the human grades with false-refusal at or near zero.
